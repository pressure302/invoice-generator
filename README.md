# Invoice Generator

A small local web app for creating sequential invoice PDFs. It runs with Python's built-in HTTP server, stores invoice history locally, and generates downloadable PDF files with ReportLab.

## Features

- Browser-based invoice form
- Sequential invoice numbers
- Automatic invoice date
- Structured billing address fields
- Merchant metadata fields
- Up to 20 line items
- Optional preset fee package
- Live line-item total calculation
- Downloadable PDF output
- Recent invoice history with invoice totals
- Editable invoices for records created with saved form data

## Privacy model

This app is designed for local use. Generated invoices, invoice history, local company configuration, and private logo assets are ignored by Git:

- `data/state.json`
- `output/invoices/*.pdf`
- `config.json`
- `assets/*`

Do not commit real client records, merchant IDs, invoice PDFs, private logos, or production company details.

## Requirements

- Python 3.11 or newer
- Python packages:
  - `reportlab`
  - `pypdf` for inspection/testing only

## Setup

Install dependencies:

```powershell
python -m pip install reportlab pypdf
```

Create a local config file if you want custom company details:

```powershell
Copy-Item config.example.json config.json
```

Edit `config.json` with your company name, address lines, and optional logo path.

## Run

```powershell
python app.py
```

Open:

```text
http://127.0.0.1:8000
```

The app will try ports `8000` through `8010` if the first port is already in use.

## Notes

This is intentionally lightweight: there is no database, account system, or cloud service dependency. Invoice history is stored in a local JSON file and PDFs are written to `output/invoices/`.
