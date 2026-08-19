from __future__ import annotations

import json
import mimetypes
import re
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
ASSETS_DIR = ROOT / "assets"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output" / "invoices"
STATE_PATH = DATA_DIR / "state.json"
STATE_BACKUP_PATH = DATA_DIR / "state.backup.json"
CONFIG_PATH = ROOT / "config.json"
DEFAULT_CONFIG = {
    "company_name": "Your Company Name",
    "company_address_lines": [
        "Street Address",
        "City, ST ZIP",
    ],
    "logo_path": "",
}


def load_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            user_config = json.load(handle)
        config.update({key: value for key, value in user_config.items() if value not in (None, "")})
    return config

STATE_OPTIONS = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
    "PR",
}

INITIAL_NEXT_INVOICE_NUMBER = 120001
MAX_ITEMS = 20
RECENT_INVOICE_LIMIT = 10
MONEY = Decimal("0.01")
STATE_LOCK = threading.Lock()


class ValidationError(Exception):
    pass


def ensure_directories() -> None:
    for directory in (DATA_DIR, OUTPUT_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        if STATE_BACKUP_PATH.exists():
            STATE_PATH.write_text(STATE_BACKUP_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            save_state({"next_invoice_number": INITIAL_NEXT_INVOICE_NUMBER, "invoices": []})


def load_state() -> dict:
    ensure_directories()
    with STATE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    tmp_path.replace(STATE_PATH)
    backup_tmp_path = STATE_BACKUP_PATH.with_suffix(".tmp")
    with backup_tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    backup_tmp_path.replace(STATE_BACKUP_PATH)


def money(value: object, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise ValidationError(f"{field_name} must be a valid number.")
    if parsed < 0:
        raise ValidationError(f"{field_name} cannot be negative.")
    return parsed.quantize(MONEY, rounding=ROUND_HALF_UP)


def required_text(data: dict, key: str, label: str, max_length: int = 500) -> str:
    value = str(data.get(key, "")).strip()
    if not value:
        raise ValidationError(f"{label} is required.")
    if len(value) > max_length:
        raise ValidationError(f"{label} is too long.")
    return value


def optional_text(data: dict, key: str, max_length: int = 500) -> str:
    value = str(data.get(key, "")).strip()
    if len(value) > max_length:
        raise ValidationError(f"{key} is too long.")
    return value


def format_client_address(data: dict) -> str:
    legacy_address = optional_text(data, "address", 500)
    if legacy_address:
        return legacy_address

    street_1 = required_text(data, "street_1", "Street address 1", 160)
    street_2 = optional_text(data, "street_2", 160)
    city = required_text(data, "city", "City", 80)
    state = required_text(data, "state", "State", 40).upper()
    zip_code = required_text(data, "zip", "ZIP code", 20)
    if state not in STATE_OPTIONS:
        raise ValidationError("State must be a valid two-letter abbreviation.")

    lines = [street_1]
    if street_2:
        lines.append(street_2)
    lines.append(f"{city}, {state} {zip_code}")
    return "\n".join(lines)


def validate_invoice(data: dict) -> dict:
    client = {
        "business_name": required_text(data, "business_name", "Client business name", 160),
        "address": format_client_address(data),
    }
    merchant = {
        "mid_number": required_text(data, "mid_number", "MID number", 80),
        "merchant_name": required_text(data, "merchant_name", "Merchant name", 160),
        "description": required_text(data, "description", "Description", 220),
    }

    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValidationError("At least one item is required.")

    items = []
    for index, item in enumerate(raw_items[:MAX_ITEMS], start=1):
        if not isinstance(item, dict):
            continue
        description = str(item.get("description", "")).strip()
        quantity_raw = str(item.get("quantity", "")).strip()
        unit_price_raw = str(item.get("unit_price", "")).strip()
        if not any((description, quantity_raw, unit_price_raw)):
            continue
        if not description:
            raise ValidationError(f"Item {index} needs a description.")
        quantity = money(quantity_raw or "0", f"Item {index} quantity")
        unit_price = money(unit_price_raw or "0", f"Item {index} unit price")
        if quantity <= 0:
            raise ValidationError(f"Item {index} quantity must be greater than zero.")
        line_total = (quantity * unit_price).quantize(MONEY, rounding=ROUND_HALF_UP)
        items.append(
            {
                "quantity": quantity,
                "description": description[:260],
                "unit_price": unit_price,
                "total": line_total,
            }
        )

    if not items:
        raise ValidationError("At least one invoice item is required.")
    if len(raw_items) > MAX_ITEMS:
        raise ValidationError(f"Invoices can include up to {MAX_ITEMS} items.")

    return {"client": client, "merchant": merchant, "items": items}


def currency(value: Decimal) -> str:
    return f"${value:,.2f}"


def invoice_total(items: list[dict]) -> Decimal:
    total = Decimal("0.00")
    for item in items:
        total += item["total"]
    return total.quantize(MONEY, rounding=ROUND_HALF_UP)


def serialize_form_data(payload: dict, validated: dict) -> dict:
    return {
        "business_name": validated["client"]["business_name"],
        "street_1": optional_text(payload, "street_1", 160),
        "street_2": optional_text(payload, "street_2", 160),
        "city": optional_text(payload, "city", 80),
        "state": optional_text(payload, "state", 40).upper(),
        "zip": optional_text(payload, "zip", 20),
        "mid_number": validated["merchant"]["mid_number"],
        "merchant_name": validated["merchant"]["merchant_name"],
        "description": validated["merchant"]["description"],
        "items": [
            {
                "quantity": quantity_label(item["quantity"]),
                "description": item["description"],
                "unit_price": f"{item['unit_price']:.2f}",
            }
            for item in validated["items"]
        ],
    }


def quantity_label(value: Decimal) -> str:
    return str(int(value)) if value == value.to_integral_value() else f"{value.normalize()}"


def paragraph_markup(text: str) -> str:
    return escape(str(text)).replace("\n", "<br/>")


def draw_wrapped(canvas: Canvas, text: str, x: float, y: float, width: float, style: ParagraphStyle) -> float:
    paragraph = Paragraph(paragraph_markup(text), style)
    _, height = paragraph.wrap(width, 10 * inch)
    paragraph.drawOn(canvas, x, y - height)
    return y - height


def draw_markup(canvas: Canvas, markup: str, x: float, y: float, width: float, style: ParagraphStyle) -> float:
    paragraph = Paragraph(markup, style)
    _, height = paragraph.wrap(width, 10 * inch)
    paragraph.drawOn(canvas, x, y - height)
    return y - height


def wrapped_height(text: str, width: float, style: ParagraphStyle) -> float:
    paragraph = Paragraph(paragraph_markup(text), style)
    _, height = paragraph.wrap(width, 10 * inch)
    return height


def draw_pdf(invoice: dict, output_path: Path) -> None:
    config = load_config()
    canvas = Canvas(str(output_path), pagesize=letter)
    page_width, page_height = letter
    margin = 0.55 * inch
    right = page_width - margin
    top = page_height - 0.55 * inch

    body = ParagraphStyle("Body", fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#202020"))
    small = ParagraphStyle("Small", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#333333"))
    logo_path = ROOT / str(config.get("logo_path", ""))
    if str(config.get("logo_path", "")).strip() and logo_path.exists():
        canvas.drawImage(str(logo_path), margin, top - 0.78 * inch, width=0.72 * inch, height=0.75 * inch, mask="auto")
    else:
        canvas.setFillColor(colors.HexColor("#246b63"))
        canvas.roundRect(margin, top - 0.73 * inch, 0.68 * inch, 0.68 * inch, 6, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawCentredString(margin + 0.34 * inch, top - 0.48 * inch, "IG")

    canvas.setFillColor(colors.HexColor("#222222"))
    canvas.setFont("Helvetica-Bold", 24)
    canvas.drawRightString(right, top - 0.1 * inch, "INVOICE")

    canvas.setFont("Helvetica", 10)
    canvas.drawRightString(right, top - 0.38 * inch, f"Invoice Number: {invoice['invoice_number']}")
    canvas.drawRightString(right, top - 0.58 * inch, f"Invoice Date: {invoice['invoice_date']}")

    y = top - 1.1 * inch
    half_width = (right - margin - 0.24 * inch) / 2
    box_h = 1.18 * inch
    canvas.setStrokeColor(colors.HexColor("#d5d8dc"))
    canvas.setFillColor(colors.HexColor("#f7f8fa"))
    canvas.roundRect(margin, y - box_h, half_width, box_h, 4, fill=1, stroke=1)
    canvas.roundRect(margin + half_width + 0.24 * inch, y - box_h, half_width, box_h, 4, fill=1, stroke=1)

    canvas.setFillColor(colors.HexColor("#4c5662"))
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(margin + 0.15 * inch, y - 0.2 * inch, "FROM:")
    canvas.drawString(margin + half_width + 0.39 * inch, y - 0.2 * inch, "BILLED TO:")

    canvas.setFillColor(colors.HexColor("#111111"))
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(margin + 0.15 * inch, y - 0.42 * inch, str(config["company_name"]))
    canvas.setFont("Helvetica", 9)
    for index, line in enumerate(config.get("company_address_lines", []), start=1):
        canvas.drawString(margin + 0.15 * inch, y - (0.42 + (index * 0.2)) * inch, str(line))

    client_x = margin + half_width + 0.39 * inch
    client_text = (
        f"<b>{paragraph_markup(invoice['client']['business_name'])}</b><br/>"
        f"{paragraph_markup(invoice['client']['address'])}"
    )
    draw_markup(canvas, client_text, client_x, y - 0.38 * inch, half_width - 0.3 * inch, body)

    y -= box_h + 0.32 * inch
    table_w = right - margin
    merchant_cols = [1.55 * inch, 2.35 * inch, table_w - 3.9 * inch]
    row_h = 0.34 * inch
    draw_table_header(canvas, margin, y, merchant_cols, ["MID Number", "Merchant Name", "Description"])
    y -= row_h
    merchant_values = [
        invoice["merchant"]["mid_number"],
        invoice["merchant"]["merchant_name"],
        invoice["merchant"]["description"],
    ]
    merchant_row_h = row_height(merchant_values, merchant_cols, small)
    draw_table_row(
        canvas,
        margin,
        y,
        merchant_cols,
        merchant_values,
        merchant_row_h,
        small,
    )

    y -= merchant_row_h + 0.38 * inch
    item_cols = [0.72 * inch, 3.7 * inch, 1.05 * inch, 1.05 * inch]
    draw_table_header(canvas, margin, y, item_cols, ["Quantity", "Item Description", "Unit Price", "Total"])
    y -= row_h
    grand_total = invoice.get("total", invoice_total(invoice["items"]))
    for item in invoice["items"]:
        item_values = [quantity_label(item["quantity"]), item["description"], currency(item["unit_price"]), currency(item["total"])]
        current_row_h = row_height(item_values, item_cols, small)
        if y - current_row_h < 1.35 * inch:
            canvas.showPage()
            y = page_height - 0.7 * inch
            draw_table_header(canvas, margin, y, item_cols, ["Quantity", "Item Description", "Unit Price", "Total"])
            y -= row_h
        draw_table_row(
            canvas,
            margin,
            y,
            item_cols,
            item_values,
            current_row_h,
            small,
        )
        y -= current_row_h

    canvas.setFillColor(colors.HexColor("#f0f2f4"))
    canvas.rect(margin + sum(item_cols[:2]), y - row_h, sum(item_cols[2:]), row_h, fill=1, stroke=1)
    canvas.setFillColor(colors.HexColor("#111111"))
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(margin + sum(item_cols[:2]) + 0.08 * inch, y - 0.23 * inch, "Total")
    canvas.drawRightString(margin + sum(item_cols) - 0.08 * inch, y - 0.23 * inch, currency(grand_total))

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(margin, 0.45 * inch, "Generated by Invoice Generator.")
    canvas.save()


def draw_table_header(canvas: Canvas, x: float, y: float, widths: list[float], labels: list[str]) -> None:
    row_h = 0.34 * inch
    canvas.setFillColor(colors.HexColor("#30343b"))
    canvas.setStrokeColor(colors.HexColor("#30343b"))
    canvas.rect(x, y - row_h, sum(widths), row_h, fill=1, stroke=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 8)
    cursor = x
    for width, label in zip(widths, labels):
        canvas.drawString(cursor + 0.08 * inch, y - 0.22 * inch, label)
        cursor += width


def draw_table_row(canvas: Canvas, x: float, y: float, widths: list[float], values: list[str], height: float, style: ParagraphStyle) -> None:
    canvas.setFillColor(colors.white)
    canvas.setStrokeColor(colors.HexColor("#d5d8dc"))
    canvas.rect(x, y - height, sum(widths), height, fill=1, stroke=1)
    cursor = x
    for width, value in zip(widths, values):
        canvas.line(cursor, y, cursor, y - height)
        draw_wrapped(canvas, str(value), cursor + 0.07 * inch, y - 0.08 * inch, width - 0.14 * inch, style)
        cursor += width
    canvas.line(cursor, y, cursor, y - height)


def row_height(values: list[str], widths: list[float], style: ParagraphStyle) -> float:
    content_height = max(wrapped_height(value, width - 0.14 * inch, style) for value, width in zip(values, widths))
    return max(0.34 * inch, content_height + 0.16 * inch)


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return cleaned[:48] or "client"


def create_invoice(payload: dict) -> dict:
    validated = validate_invoice(payload)
    with STATE_LOCK:
        state = load_state()
        invoice_number_int = int(state.get("next_invoice_number", INITIAL_NEXT_INVOICE_NUMBER))
        invoice_number = f"INV-{invoice_number_int}"
        invoice_date = datetime.now().strftime("%m/%d/%Y")
        invoice = {
            **validated,
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        invoice["total"] = invoice_total(invoice["items"])
        form_data = serialize_form_data(payload, validated)
        filename = f"{invoice_number}_{slug(invoice['client']['business_name'])}.pdf"
        output_path = OUTPUT_DIR / filename
        draw_pdf(invoice, output_path)

        state["next_invoice_number"] = invoice_number_int + 1
        state.setdefault("invoices", []).append(
            {
                "invoice_number": invoice_number,
                "invoice_date": invoice_date,
                "client": invoice["client"]["business_name"],
                "amount": currency(invoice["total"]),
                "filename": filename,
                "created_at": invoice["created_at"],
                "data": form_data,
            }
        )
        state["invoices"] = state["invoices"][-RECENT_INVOICE_LIMIT:]
        save_state(state)

    return {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "amount": currency(invoice["total"]),
        "download_url": f"/download/{filename}",
    }


def find_invoice_record(state: dict, invoice_number: str) -> dict | None:
    for record in state.get("invoices", []):
        if record.get("invoice_number") == invoice_number:
            return record
    return None


def update_invoice(invoice_number: str, payload: dict) -> dict:
    if not re.fullmatch(r"INV-\d+", invoice_number):
        raise ValidationError("Invoice number is invalid.")
    validated = validate_invoice(payload)
    with STATE_LOCK:
        state = load_state()
        record = find_invoice_record(state, invoice_number)
        if not record:
            raise ValidationError(f"{invoice_number} was not found.")

        invoice = {
            **validated,
            "invoice_number": invoice_number,
            "invoice_date": record.get("invoice_date") or datetime.now().strftime("%m/%d/%Y"),
            "created_at": record.get("created_at") or datetime.now().isoformat(timespec="seconds"),
        }
        invoice["total"] = invoice_total(invoice["items"])
        filename = record.get("filename") or f"{invoice_number}_{slug(invoice['client']['business_name'])}.pdf"
        draw_pdf(invoice, OUTPUT_DIR / filename)

        record["invoice_date"] = invoice["invoice_date"]
        record["client"] = invoice["client"]["business_name"]
        record["amount"] = currency(invoice["total"])
        record["filename"] = filename
        record["data"] = serialize_form_data(payload, validated)
        record["modified_at"] = datetime.now().isoformat(timespec="seconds")
        save_state(state)

    return {
        "invoice_number": invoice_number,
        "invoice_date": invoice["invoice_date"],
        "amount": currency(invoice["total"]),
        "download_url": f"/download/{filename}",
    }


class InvoiceHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/index.html"):
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if self.path == "/api/state":
            state = load_state()
            self.json_response(
                {
                    "next_invoice_number": f"INV-{state.get('next_invoice_number', INITIAL_NEXT_INVOICE_NUMBER)}",
                    "invoices": list(reversed(state.get("invoices", [])))[0:25],
                }
            )
            return
        if self.path.startswith("/api/invoices/"):
            invoice_number = unquote(self.path.removeprefix("/api/invoices/"))
            state = load_state()
            record = find_invoice_record(state, invoice_number)
            if not record:
                self.json_response({"error": f"{invoice_number} was not found."}, HTTPStatus.NOT_FOUND)
                return
            data = record.get("data")
            if not data:
                self.json_response({"error": f"{invoice_number} does not have editable form data saved."}, HTTPStatus.NOT_FOUND)
                return
            self.json_response({"invoice": record, "data": data})
            return
        if self.path.startswith("/static/"):
            relative = unquote(self.path.removeprefix("/static/"))
            self.serve_file((STATIC_DIR / relative).resolve())
            return
        if self.path.startswith("/assets/"):
            relative = unquote(self.path.removeprefix("/assets/"))
            self.serve_file((ASSETS_DIR / relative).resolve())
            return
        if self.path.startswith("/download/"):
            relative = unquote(self.path.removeprefix("/download/"))
            target = (OUTPUT_DIR / relative).resolve()
            if not str(target).startswith(str(OUTPUT_DIR.resolve())):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            self.serve_file(target, "application/pdf", attachment=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/api/invoices":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = create_invoice(payload)
            self.json_response(result, HTTPStatus.CREATED)
        except ValidationError as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.json_response({"error": f"Unable to create invoice: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self) -> None:
        if not self.path.startswith("/api/invoices/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            invoice_number = unquote(self.path.removeprefix("/api/invoices/"))
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = update_invoice(invoice_number, payload)
            self.json_response(result)
        except ValidationError as exc:
            self.json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.json_response({"error": f"Unable to update invoice: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_file(self, path: Path, content_type: str | None = None, attachment: bool = False) -> None:
        path = path.resolve()
        allowed_roots = [STATIC_DIR.resolve(), ASSETS_DIR.resolve(), OUTPUT_DIR.resolve()]
        if path != STATIC_DIR.resolve() / "index.html" and not any(str(path).startswith(str(root)) for root in allowed_roots):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        guessed = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_header("Content-Type", guessed)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as handle:
            self.wfile.write(handle.read())

    def json_response(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    ensure_directories()
    last_error = None
    for port in range(8000, 8011):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), InvoiceHandler)
            print(f"Invoice generator running at http://127.0.0.1:{port}")
            server.serve_forever()
            return
        except OSError as exc:
            last_error = exc
    raise SystemExit(f"Unable to start invoice generator: {last_error}")


if __name__ == "__main__":
    main()
