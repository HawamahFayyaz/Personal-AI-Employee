/**
 * odoo-mcp — MCP server for Odoo 19 via JSON-RPC External API
 *
 * Tools exposed:
 *   create_invoice(partner_name, lines[{product, quantity, price}])
 *   list_invoices(status?, date_from?, date_to?)
 *   get_invoice(invoice_id)
 *   create_payment(invoice_id, amount, payment_method)
 *   get_account_balance()
 *   list_transactions(date_from?, date_to?)
 *   create_partner(name, email, phone?)
 *
 * IMPORTANT: create_invoice and create_payment always produce DRAFT records.
 * A human must open Odoo and click "Confirm" / "Validate" before anything posts.
 *
 * Environment variables (resolved in priority order):
 *   process env → vault root .env → built-in defaults
 *
 *   ODOO_URL       Odoo base URL          default: http://localhost:8069
 *   ODOO_DB        Odoo database name     REQUIRED
 *   ODOO_USER      Odoo login username    REQUIRED
 *   ODOO_PASSWORD  Odoo password          REQUIRED
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

// ---------------------------------------------------------------------------
// Paths & .env loading
// ---------------------------------------------------------------------------
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VAULT_ROOT = path.resolve(__dirname, "..", "..");

function loadDotEnv(filePath) {
  if (!fs.existsSync(filePath)) return;
  for (const line of fs.readFileSync(filePath, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eqIdx = trimmed.indexOf("=");
    if (eqIdx < 0) continue;
    const key = trimmed.slice(0, eqIdx).trim();
    let val = trimmed.slice(eqIdx + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = val;
  }
}

loadDotEnv(path.join(VAULT_ROOT, ".env"));

const ODOO_URL      = (process.env.ODOO_URL ?? "http://localhost:8069").replace(/\/$/, "");
const ODOO_DB       = process.env.ODOO_DB;
const ODOO_USER     = process.env.ODOO_USER;
const ODOO_PASSWORD = process.env.ODOO_PASSWORD;

// ---------------------------------------------------------------------------
// Logging (stderr keeps the MCP stdio stream clean)
// ---------------------------------------------------------------------------
function log(label, detail) {
  const ts = new Date().toISOString();
  process.stderr.write(`[${ts}] [odoo-mcp] ${label}${detail ? ": " + detail : ""}\n`);
}

// ---------------------------------------------------------------------------
// Config guard — fail fast at startup if credentials are missing
// ---------------------------------------------------------------------------
if (!ODOO_DB || !ODOO_USER || !ODOO_PASSWORD) {
  log("FATAL", "ODOO_DB, ODOO_USER, and ODOO_PASSWORD must all be set (env or vault .env).");
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Odoo JSON-RPC client
// Ref: https://www.odoo.com/documentation/19.0/developer/reference/external_api.html
// ---------------------------------------------------------------------------
let _uid = null; // cached user id after authentication

async function rpcCall(service, method, args) {
  const url = `${ODOO_URL}/jsonrpc`;
  const payload = {
    jsonrpc: "2.0",
    method: "call",
    id: Math.floor(Math.random() * 1_000_000),
    params: { service, method, args },
  };

  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    throw new Error(`HTTP ${res.status} from Odoo at ${url}: ${res.statusText}`);
  }

  const data = await res.json();
  if (data.error) {
    const msg = data.error.data?.message ?? data.error.message ?? JSON.stringify(data.error);
    throw new Error(`Odoo RPC error [${service}.${method}]: ${msg}`);
  }
  return data.result;
}

async function authenticate() {
  const uid = await rpcCall("common", "authenticate", [ODOO_DB, ODOO_USER, ODOO_PASSWORD, {}]);
  if (!uid) {
    throw new Error("Odoo authentication failed — verify ODOO_DB, ODOO_USER, and ODOO_PASSWORD.");
  }
  log("auth", `uid=${uid} db=${ODOO_DB}`);
  return uid;
}

/** Returns cached uid, re-authenticating only if needed. */
async function getUID() {
  if (!_uid) _uid = await authenticate();
  return _uid;
}

/**
 * Calls execute_kw on an Odoo model.
 * @param {string} model   e.g. "account.move"
 * @param {string} method  e.g. "search_read"
 * @param {Array}  args    positional args
 * @param {Object} kwargs  keyword args (fields, limit, order, …)
 */
async function execute(model, method, args = [], kwargs = {}) {
  const uid = await getUID();
  return rpcCall("object", "execute_kw", [ODOO_DB, uid, ODOO_PASSWORD, model, method, args, kwargs]);
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/** Resolve a partner by name (case-insensitive partial match). */
async function findPartner(name) {
  const ids = await execute("res.partner", "search", [[["name", "ilike", name]]], { limit: 1 });
  if (!ids.length) throw new Error(`No partner found matching "${name}".`);
  const [p] = await execute("res.partner", "read", [ids], { fields: ["id", "name", "email", "phone"] });
  return p;
}

/** Resolve a product by name (case-insensitive partial match). */
async function findProduct(name) {
  const ids = await execute("product.product", "search", [[["name", "ilike", name]]], { limit: 1 });
  if (!ids.length) throw new Error(`No product found matching "${name}".`);
  const [p] = await execute("product.product", "read", [ids], {
    fields: ["id", "name", "uom_id", "list_price"],
  });
  return p;
}

/** Resolve the first journal of a given type ("bank" | "cash"). */
async function findJournal(type) {
  const ids = await execute("account.journal", "search", [[["type", "=", type]]], { limit: 1 });
  if (!ids.length) throw new Error(`No journal of type "${type}" found in Odoo.`);
  const [j] = await execute("account.journal", "read", [ids], { fields: ["id", "name", "type"] });
  return j;
}

/** Unwrap a Odoo Many2one field that may be [id, name] or false. */
function m2oId(val) {
  if (Array.isArray(val)) return val[0];
  if (val && typeof val === "number") return val;
  return null;
}

// ---------------------------------------------------------------------------
// Tool: create_invoice
// ---------------------------------------------------------------------------
async function toolCreateInvoice({ partner_name, lines }) {
  const partner = await findPartner(partner_name);

  const invoiceLines = [];
  for (const line of lines) {
    const product = await findProduct(line.product);
    invoiceLines.push([0, 0, {
      product_id: product.id,
      quantity: line.quantity,
      price_unit: line.price,
      name: product.name,
    }]);
  }

  const invoiceId = await execute("account.move", "create", [{
    move_type: "out_invoice",   // customer invoice
    partner_id: partner.id,
    invoice_line_ids: invoiceLines,
    // state defaults to "draft" — we do NOT call action_post
  }]);

  const [invoice] = await execute("account.move", "read", [[invoiceId]], {
    fields: ["id", "name", "state", "partner_id", "amount_untaxed", "amount_tax", "amount_total", "invoice_line_ids"],
  });

  log("create_invoice", `id=${invoiceId} partner="${partner_name}" total=${invoice.amount_total}`);
  return {
    success: true,
    invoice_id: invoiceId,
    reference: invoice.name,
    status: invoice.state,
    partner: partner.name,
    amount_total: invoice.amount_total,
    line_count: lines.length,
    warning: "Invoice is in DRAFT. Open Odoo and click 'Confirm' to post it.",
  };
}

// ---------------------------------------------------------------------------
// Tool: list_invoices
// ---------------------------------------------------------------------------
async function toolListInvoices({ status, date_from, date_to }) {
  const domain = [["move_type", "=", "out_invoice"]];
  if (status)    domain.push(["state", "=", status]);
  if (date_from) domain.push(["invoice_date", ">=", date_from]);
  if (date_to)   domain.push(["invoice_date", "<=", date_to]);

  const invoices = await execute("account.move", "search_read", [domain], {
    fields: [
      "id", "name", "state", "partner_id",
      "amount_total", "amount_residual",
      "invoice_date", "invoice_date_due", "payment_state",
    ],
    order: "invoice_date desc, id desc",
    limit: 100,
  });

  log("list_invoices", `count=${invoices.length} status=${status ?? "all"}`);
  return {
    success: true,
    count: invoices.length,
    invoices,
  };
}

// ---------------------------------------------------------------------------
// Tool: get_invoice
// ---------------------------------------------------------------------------
async function toolGetInvoice({ invoice_id }) {
  const rows = await execute("account.move", "read", [[invoice_id]], {
    fields: [
      "id", "name", "state", "move_type",
      "partner_id", "currency_id",
      "amount_untaxed", "amount_tax", "amount_total", "amount_residual",
      "invoice_date", "invoice_date_due", "payment_state",
      "invoice_line_ids", "ref", "narration",
    ],
  });

  if (!rows.length) throw new Error(`Invoice ID ${invoice_id} not found.`);
  const invoice = rows[0];

  // Expand line details
  if (invoice.invoice_line_ids?.length) {
    invoice.lines = await execute("account.move.line", "read", [invoice.invoice_line_ids], {
      fields: ["id", "product_id", "name", "quantity", "price_unit", "price_subtotal", "tax_ids", "discount"],
    });
  }

  log("get_invoice", `id=${invoice_id} state=${invoice.state} total=${invoice.amount_total}`);
  return { success: true, invoice };
}

// ---------------------------------------------------------------------------
// Tool: create_payment
// ---------------------------------------------------------------------------
async function toolCreatePayment({ invoice_id, amount, payment_method }) {
  // Load invoice for partner info
  const rows = await execute("account.move", "read", [[invoice_id]], {
    fields: ["id", "name", "state", "move_type", "partner_id", "amount_residual", "currency_id"],
  });
  if (!rows.length) throw new Error(`Invoice ID ${invoice_id} not found.`);
  const invoice = rows[0];

  // Map payment_method → journal type
  const methodMap = {
    bank: "bank",
    transfer: "bank",
    credit_card: "bank",
    check: "bank",
    cash: "cash",
  };
  const journalType = methodMap[payment_method.toLowerCase()] ?? "bank";
  const journal = await findJournal(journalType);

  const partnerId = m2oId(invoice.partner_id);
  const isCustomerInvoice = invoice.move_type === "out_invoice";

  const paymentId = await execute("account.payment", "create", [{
    payment_type: isCustomerInvoice ? "inbound" : "outbound",
    partner_type: isCustomerInvoice ? "customer" : "supplier",
    partner_id: partnerId,
    amount,
    journal_id: journal.id,
    ref: `Payment for ${invoice.name}`,
    date: new Date().toISOString().slice(0, 10),
    // state defaults to "draft" — we do NOT call action_post
  }]);

  const [payment] = await execute("account.payment", "read", [[paymentId]], {
    fields: ["id", "name", "state", "payment_type", "partner_id", "amount", "journal_id", "date", "ref"],
  });

  log("create_payment", `id=${paymentId} invoice=${invoice_id} amount=${amount} method=${payment_method}`);
  return {
    success: true,
    payment_id: paymentId,
    reference: payment.name,
    status: payment.state,
    invoice_reference: invoice.name,
    amount,
    journal: journal.name,
    warning: "Payment is in DRAFT. Open Odoo and click 'Validate' to post it.",
  };
}

// ---------------------------------------------------------------------------
// Tool: get_account_balance
// ---------------------------------------------------------------------------
async function toolGetAccountBalance() {
  // Fetch all bank and cash journals
  const journals = await execute("account.journal", "search_read",
    [[["type", "in", ["bank", "cash"]]]],
    { fields: ["id", "name", "type", "default_account_id", "currency_id"] }
  );

  const balances = [];
  for (const journal of journals) {
    const accountId = m2oId(journal.default_account_id);
    if (!accountId) {
      balances.push({
        journal_id: journal.id,
        journal_name: journal.name,
        journal_type: journal.type,
        current_balance: null,
        note: "No default account configured.",
      });
      continue;
    }

    try {
      const [account] = await execute("account.account", "read", [[accountId]], {
        fields: ["id", "name", "code", "account_type", "current_balance"],
      });
      balances.push({
        journal_id: journal.id,
        journal_name: journal.name,
        journal_type: journal.type,
        account_code: account?.code,
        account_name: account?.name,
        current_balance: account?.current_balance ?? 0,
      });
    } catch (err) {
      balances.push({
        journal_id: journal.id,
        journal_name: journal.name,
        journal_type: journal.type,
        current_balance: null,
        note: `Could not read balance: ${err.message}`,
      });
    }
  }

  log("get_account_balance", `journals=${balances.length}`);
  return {
    success: true,
    as_of: new Date().toISOString(),
    balances,
  };
}

// ---------------------------------------------------------------------------
// Tool: list_transactions
// ---------------------------------------------------------------------------
async function toolListTransactions({ date_from, date_to }) {
  const domain = [["state", "=", "posted"]];
  if (date_from) domain.push(["date", ">=", date_from]);
  if (date_to)   domain.push(["date", "<=", date_to]);

  const payments = await execute("account.payment", "search_read", [domain], {
    fields: [
      "id", "name", "state", "payment_type",
      "partner_id", "partner_type",
      "amount", "currency_id",
      "journal_id", "date", "ref",
    ],
    order: "date desc, id desc",
    limit: 200,
  });

  log("list_transactions", `count=${payments.length}`);
  return {
    success: true,
    count: payments.length,
    transactions: payments,
  };
}

// ---------------------------------------------------------------------------
// Tool: create_partner
// ---------------------------------------------------------------------------
async function toolCreatePartner({ name, email, phone }) {
  // Deduplicate by email
  const existing = await execute("res.partner", "search", [[["email", "=", email]]], { limit: 1 });
  if (existing.length) {
    const [partner] = await execute("res.partner", "read", [existing], {
      fields: ["id", "name", "email", "phone"],
    });
    log("create_partner", `duplicate email=${email} existing_id=${partner.id}`);
    return {
      success: true,
      already_exists: true,
      partner_id: partner.id,
      partner,
      message: `Partner with email "${email}" already exists — returning existing record.`,
    };
  }

  const vals = { name, email };
  if (phone) vals.phone = phone;

  const partnerId = await execute("res.partner", "create", [vals]);
  const [partner] = await execute("res.partner", "read", [[partnerId]], {
    fields: ["id", "name", "email", "phone"],
  });

  log("create_partner", `id=${partnerId} name="${name}" email="${email}"`);
  return {
    success: true,
    already_exists: false,
    partner_id: partnerId,
    partner,
    message: `Partner "${name}" created successfully.`,
  };
}

// ---------------------------------------------------------------------------
// MCP Server registration
// ---------------------------------------------------------------------------
const server = new McpServer({
  name: "odoo",
  version: "1.0.0",
});

// --- create_invoice ---
server.tool(
  "create_invoice",
  [
    "Create a customer invoice in Odoo.",
    "ALWAYS produces a DRAFT — never confirms or posts automatically.",
    "A human must open Odoo and click Confirm before it posts to the ledger.",
  ].join(" "),
  {
    partner_name: z.string().describe("Customer name to look up (partial, case-insensitive match)."),
    lines: z
      .array(z.object({
        product:  z.string().describe("Product name (partial match supported)."),
        quantity: z.number().positive().describe("Quantity."),
        price:    z.number().nonnegative().describe("Unit price (overrides product default)."),
      }))
      .min(1)
      .describe("One or more invoice line items."),
  },
  async (args) => {
    try {
      return { content: [{ type: "text", text: JSON.stringify(await toolCreateInvoice(args), null, 2) }] };
    } catch (err) {
      log("create_invoice ERROR", err.message);
      return { content: [{ type: "text", text: JSON.stringify({ success: false, error: err.message }, null, 2) }], isError: true };
    }
  }
);

// --- list_invoices ---
server.tool(
  "list_invoices",
  "List customer invoices from Odoo with optional status and date filters. Returns up to 100 records.",
  {
    status:    z.enum(["draft", "posted", "cancel"]).optional().describe("Invoice state filter."),
    date_from: z.string().optional().describe("Start of invoice date range (YYYY-MM-DD)."),
    date_to:   z.string().optional().describe("End of invoice date range (YYYY-MM-DD)."),
  },
  async (args) => {
    try {
      return { content: [{ type: "text", text: JSON.stringify(await toolListInvoices(args), null, 2) }] };
    } catch (err) {
      log("list_invoices ERROR", err.message);
      return { content: [{ type: "text", text: JSON.stringify({ success: false, error: err.message }, null, 2) }], isError: true };
    }
  }
);

// --- get_invoice ---
server.tool(
  "get_invoice",
  "Fetch full details of a single Odoo invoice including all line items.",
  {
    invoice_id: z.number().int().positive().describe("Odoo account.move integer ID."),
  },
  async (args) => {
    try {
      return { content: [{ type: "text", text: JSON.stringify(await toolGetInvoice(args), null, 2) }] };
    } catch (err) {
      log("get_invoice ERROR", err.message);
      return { content: [{ type: "text", text: JSON.stringify({ success: false, error: err.message }, null, 2) }], isError: true };
    }
  }
);

// --- create_payment ---
server.tool(
  "create_payment",
  [
    "Create a payment record in Odoo linked to an invoice.",
    "ALWAYS produces a DRAFT — never validates or posts automatically.",
    "A human must open Odoo and click Validate before funds move.",
  ].join(" "),
  {
    invoice_id:     z.number().int().positive().describe("Odoo account.move ID of the invoice to pay."),
    amount:         z.number().positive().describe("Payment amount (in the invoice currency)."),
    payment_method: z
      .enum(["bank", "cash", "credit_card", "check", "transfer"])
      .describe("Payment method — maps to bank or cash journal in Odoo."),
  },
  async (args) => {
    try {
      return { content: [{ type: "text", text: JSON.stringify(await toolCreatePayment(args), null, 2) }] };
    } catch (err) {
      log("create_payment ERROR", err.message);
      return { content: [{ type: "text", text: JSON.stringify({ success: false, error: err.message }, null, 2) }], isError: true };
    }
  }
);

// --- get_account_balance ---
server.tool(
  "get_account_balance",
  "Return current balances for all bank and cash accounts configured in Odoo.",
  {},
  async () => {
    try {
      return { content: [{ type: "text", text: JSON.stringify(await toolGetAccountBalance(), null, 2) }] };
    } catch (err) {
      log("get_account_balance ERROR", err.message);
      return { content: [{ type: "text", text: JSON.stringify({ success: false, error: err.message }, null, 2) }], isError: true };
    }
  }
);

// --- list_transactions ---
server.tool(
  "list_transactions",
  "List posted payment transactions from Odoo with optional date filters. Returns up to 200 records.",
  {
    date_from: z.string().optional().describe("Start date (YYYY-MM-DD)."),
    date_to:   z.string().optional().describe("End date (YYYY-MM-DD)."),
  },
  async (args) => {
    try {
      return { content: [{ type: "text", text: JSON.stringify(await toolListTransactions(args), null, 2) }] };
    } catch (err) {
      log("list_transactions ERROR", err.message);
      return { content: [{ type: "text", text: JSON.stringify({ success: false, error: err.message }, null, 2) }], isError: true };
    }
  }
);

// --- create_partner ---
server.tool(
  "create_partner",
  "Create a new partner (customer or vendor) in Odoo. Returns the existing record if the email is already registered.",
  {
    name:  z.string().describe("Full name or company name."),
    email: z.string().email().describe("Email address — used to detect duplicates."),
    phone: z.string().optional().describe("Phone number (optional)."),
  },
  async (args) => {
    try {
      return { content: [{ type: "text", text: JSON.stringify(await toolCreatePartner(args), null, 2) }] };
    } catch (err) {
      log("create_partner ERROR", err.message);
      return { content: [{ type: "text", text: JSON.stringify({ success: false, error: err.message }, null, 2) }], isError: true };
    }
  }
);

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
log("startup", `url=${ODOO_URL} db=${ODOO_DB} user=${ODOO_USER}`);

const transport = new StdioServerTransport();
await server.connect(transport);
