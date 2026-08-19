const MAX_ITEMS = 20;
const APPLICABLE_FEES = [
  { quantity: "1", description: "Equipment Application Fee", unit_price: "99.99", feeKey: "equipment-application-fee" },
  { quantity: "1", description: "File Build Fee", unit_price: "99.99", feeKey: "file-build-fee" },
  { quantity: "1", description: "Programming Fee", unit_price: "495.00", feeKey: "programming-fee" },
  { quantity: "1", description: "Installation Fee", unit_price: "199.00", feeKey: "installation-fee" },
];
const itemsNode = document.querySelector("#items");
const addItemButton = document.querySelector("#addItem");
const applicableFeesCheckbox = document.querySelector("#applicableFees");
const form = document.querySelector("#invoiceForm");
const totalNode = document.querySelector("#grandTotal");
const statusNode = document.querySelector("#status");
const nextInvoiceNode = document.querySelector("#nextInvoice");
const historyList = document.querySelector("#historyList");
const lastDownload = document.querySelector("#lastDownload");
const submitButton = document.querySelector("#submitButton");
const cancelEditButton = document.querySelector("#cancelEdit");
let editingInvoiceNumber = null;

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function money(value) {
  const number = Number.parseFloat(value || "0");
  return Number.isFinite(number) ? number : 0;
}

function formatCurrency(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function itemCount() {
  return itemsNode.querySelectorAll(".item-row").length;
}

function addItem(values = {}, focusNewRow = false) {
  if (itemCount() >= MAX_ITEMS) {
    statusNode.textContent = `Invoices can include up to ${MAX_ITEMS} items.`;
    return;
  }

  const row = document.createElement("div");
  row.className = "item-row";
  if (values.feeKey) {
    row.dataset.feeKey = values.feeKey;
    row.dataset.originalQuantity = values.quantity;
    row.dataset.originalDescription = values.description;
    row.dataset.originalUnitPrice = values.unit_price;
  }
  row.innerHTML = `
    <input class="quantity" inputmode="decimal" min="0" step="1" placeholder="1" value="${escapeHtml(values.quantity || "")}" aria-label="Quantity">
    <input class="description" placeholder="Item description" value="${escapeHtml(values.description || "")}" aria-label="Item description">
    <input class="unit-price" inputmode="decimal" min="0" step="0.01" placeholder="0.00" value="${escapeHtml(values.unit_price || "")}" aria-label="Unit price">
    <span class="line-total">$0.00</span>
    <button type="button" class="remove-button" title="Remove item">x</button>
  `;
  row.querySelector(".remove-button").addEventListener("click", () => {
    if (itemCount() > 1) {
      row.remove();
      updateTotals();
    }
  });
  row.querySelectorAll("input").forEach((input) => input.addEventListener("input", updateTotals));
  itemsNode.append(row);
  updateTotals();
  if (focusNewRow) {
    row.querySelector(".quantity").focus();
  }
}

function rowStillMatchesFee(row) {
  return (
    row.dataset.feeKey &&
    row.querySelector(".quantity").value === row.dataset.originalQuantity &&
    row.querySelector(".description").value === row.dataset.originalDescription &&
    row.querySelector(".unit-price").value === row.dataset.originalUnitPrice
  );
}

function setApplicableFees(checked) {
  if (checked) {
    const missingFees = APPLICABLE_FEES.filter((fee) => {
      return ![...itemsNode.querySelectorAll(".item-row")].some((row) => row.dataset.feeKey === fee.feeKey);
    });
    if (itemCount() + missingFees.length > MAX_ITEMS) {
      applicableFeesCheckbox.checked = false;
      statusNode.textContent = `Applicable Fees need ${missingFees.length} available item rows.`;
      return;
    }
    missingFees.forEach((fee) => addItem(fee));
    return;
  }

  [...itemsNode.querySelectorAll(".item-row")].forEach((row) => {
    if (rowStillMatchesFee(row) && itemCount() > 1) {
      row.remove();
    }
  });
  updateTotals();
}

function updateTotals() {
  let total = 0;
  itemsNode.querySelectorAll(".item-row").forEach((row) => {
    const quantity = money(row.querySelector(".quantity").value);
    const unitPrice = money(row.querySelector(".unit-price").value);
    const lineTotal = quantity * unitPrice;
    total += lineTotal;
    row.querySelector(".line-total").textContent = formatCurrency(lineTotal);
  });
  totalNode.textContent = formatCurrency(total);
}

function collectItems() {
  return [...itemsNode.querySelectorAll(".item-row")].map((row) => ({
    quantity: row.querySelector(".quantity").value,
    description: row.querySelector(".description").value,
    unit_price: row.querySelector(".unit-price").value,
  }));
}

function resetFormForNewInvoice() {
  editingInvoiceNumber = null;
  submitButton.textContent = "Generate PDF";
  cancelEditButton.hidden = true;
  form.reset();
  itemsNode.innerHTML = "";
  applicableFeesCheckbox.checked = false;
  addItem({ quantity: "1" });
  updateTotals();
}

function populateForm(data) {
  form.elements.business_name.value = data.business_name || "";
  form.elements.street_1.value = data.street_1 || "";
  form.elements.street_2.value = data.street_2 || "";
  form.elements.city.value = data.city || "";
  form.elements.state.value = data.state || "";
  form.elements.zip.value = data.zip || "";
  form.elements.mid_number.value = data.mid_number || "";
  form.elements.merchant_name.value = data.merchant_name || "";
  form.elements.description.value = data.description || "";
  applicableFeesCheckbox.checked = false;
  itemsNode.innerHTML = "";
  (data.items || []).forEach((item) => addItem(item));
  if (!itemCount()) {
    addItem({ quantity: "1" });
  }
  updateTotals();
}

async function editInvoice(invoiceNumber) {
  statusNode.textContent = `Loading ${invoiceNumber}...`;
  const response = await fetch(`/api/invoices/${encodeURIComponent(invoiceNumber)}`);
  const result = await response.json();
  if (!response.ok) {
    statusNode.textContent = result.error || `Unable to load ${invoiceNumber}.`;
    return;
  }
  editingInvoiceNumber = invoiceNumber;
  populateForm(result.data);
  submitButton.textContent = `Save ${invoiceNumber}`;
  cancelEditButton.hidden = false;
  statusNode.textContent = `Editing ${invoiceNumber}. Saving will replace that invoice PDF.`;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadState() {
  const response = await fetch("/api/state");
  const state = await response.json();
  nextInvoiceNode.textContent = state.next_invoice_number;
  historyList.innerHTML = "";
  if (!state.invoices.length) {
    historyList.textContent = "No invoices generated yet.";
    return;
  }
  state.invoices.forEach((invoice) => {
    const row = document.createElement("div");
    row.className = "history-item";
    const editButton = invoice.data
      ? `<button type="button" class="history-edit" data-invoice="${escapeHtml(invoice.invoice_number)}">Edit</button>`
      : `<span class="history-edit-placeholder"></span>`;
    row.innerHTML = `
      <span><strong>${escapeHtml(invoice.invoice_number)}</strong><br>${escapeHtml(invoice.client)}</span>
      <span class="history-amount">${escapeHtml(invoice.amount || "")}</span>
      ${editButton}
      <a href="/download/${encodeURIComponent(invoice.filename)}">PDF</a>
    `;
    historyList.append(row);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusNode.textContent = "Generating invoice...";
  const formData = new FormData(form);
  const payload = Object.fromEntries(formData.entries());
  payload.items = collectItems();

  const url = editingInvoiceNumber ? `/api/invoices/${encodeURIComponent(editingInvoiceNumber)}` : "/api/invoices";
  const method = editingInvoiceNumber ? "PUT" : "POST";
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) {
    statusNode.textContent = result.error || "Unable to create invoice.";
    return;
  }

  statusNode.textContent = editingInvoiceNumber
    ? `${result.invoice_number} updated.`
    : `${result.invoice_number} created for ${result.invoice_date}.`;
  lastDownload.href = result.download_url;
  lastDownload.hidden = false;
  window.location.href = result.download_url;
  resetFormForNewInvoice();
  await loadState();
});

addItemButton.addEventListener("click", () => addItem({ quantity: "1" }, true));
applicableFeesCheckbox.addEventListener("change", () => setApplicableFees(applicableFeesCheckbox.checked));
cancelEditButton.addEventListener("click", () => {
  resetFormForNewInvoice();
  statusNode.textContent = "Edit canceled.";
});
historyList.addEventListener("click", (event) => {
  const button = event.target.closest(".history-edit");
  if (button) {
    editInvoice(button.dataset.invoice);
  }
});

resetFormForNewInvoice();
loadState();
