# odoo-mcp

MCP server for Odoo 19 — exposes accounting and partner management as Claude tools via the Odoo JSON-RPC External API.

## Tools

| Tool | Description |
|------|-------------|
| `create_invoice(partner_name, lines)` | Create a customer invoice in **DRAFT** (never auto-posts) |
| `list_invoices(status?, date_from?, date_to?)` | List customer invoices with optional filters |
| `get_invoice(invoice_id)` | Fetch full invoice details including line items |
| `create_payment(invoice_id, amount, payment_method)` | Create a payment in **DRAFT** (never auto-posts) |
| `get_account_balance()` | Current balances for all bank and cash accounts |
| `list_transactions(date_from?, date_to?)` | List posted payment transactions |
| `create_partner(name, email, phone?)` | Create a partner; returns existing record if email already registered |

### Draft-only safety contract

`create_invoice` and `create_payment` **always** leave records in `draft` state.
The MCP server never calls `action_post` or `action_validate`.
A human must open Odoo and click **Confirm** / **Validate** before anything posts to the general ledger.

---

## Quick start

```bash
cd MCP_Servers/odoo-mcp
npm install
node index.js
```

The server reads configuration from environment variables (or the vault root `.env` file).

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ODOO_URL` | No | `http://localhost:8069` | Base URL of the Odoo instance |
| `ODOO_DB` | **Yes** | — | Odoo database name |
| `ODOO_USER` | **Yes** | — | Odoo login username |
| `ODOO_PASSWORD` | **Yes** | — | Odoo password |

Add these to the vault root `.env` file (same file used by other vault services):

```dotenv
ODOO_URL=http://localhost:8069
ODOO_DB=mycompany
ODOO_USER=admin
ODOO_PASSWORD=supersecret
```

---

## Claude Code MCP config

Add to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "node",
      "args": ["/mnt/d/HACKATHON_00/AI_Employee_Vault/MCP_Servers/odoo-mcp/index.js"],
      "env": {
        "ODOO_URL": "http://localhost:8069",
        "ODOO_DB": "mycompany",
        "ODOO_USER": "admin",
        "ODOO_PASSWORD": "your_password_here"
      }
    }
  }
}
```

Or omit `env` entirely and rely on the vault `.env` file.

---

## Tool reference

### `create_invoice`

```
create_invoice(
  partner_name: string,       // partial name match
  lines: Array<{
    product:  string,         // partial name match
    quantity: number,
    price:    number          // unit price
  }>
)
```

Returns the new invoice ID, reference number, and a `warning` reminding the caller it is in DRAFT.

---

### `list_invoices`

```
list_invoices(
  status?:    "draft" | "posted" | "cancel",
  date_from?: "YYYY-MM-DD",
  date_to?:   "YYYY-MM-DD"
)
```

Returns up to 100 invoices ordered by invoice date descending.

---

### `get_invoice`

```
get_invoice(invoice_id: number)
```

Returns full invoice fields plus an expanded `lines` array with product, quantity, price, and tax details.

---

### `create_payment`

```
create_payment(
  invoice_id:     number,
  amount:         number,
  payment_method: "bank" | "cash" | "credit_card" | "check" | "transfer"
)
```

`bank`, `transfer`, `credit_card`, and `check` map to the first **Bank** journal.  
`cash` maps to the first **Cash** journal.

---

### `get_account_balance`

```
get_account_balance()
```

Returns balances for every bank/cash journal via `account.account.current_balance`.

---

### `list_transactions`

```
list_transactions(
  date_from?: "YYYY-MM-DD",
  date_to?:   "YYYY-MM-DD"
)
```

Returns posted `account.payment` records (inbound and outbound) up to 200 entries, newest first.

---

### `create_partner`

```
create_partner(
  name:   string,
  email:  string,     // used for duplicate detection
  phone?: string
)
```

If a partner with the same email already exists, returns it with `already_exists: true` instead of creating a duplicate.

---

## Odoo prerequisites

1. Odoo 19 running and accessible at `ODOO_URL` (use the `odoo_setup_skill` to spin it up with Docker).
2. The login user must have access to the **Accounting** app.
3. At least one **Bank** journal and one **Cash** journal must be configured (Settings → Accounting → Journals).
4. Products used in invoices must exist in Odoo's product catalog.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Authentication failed` | Verify `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD`; check Odoo logs |
| `No partner found` | The name doesn't exist in Odoo; create with `create_partner` first |
| `No product found` | Add the product in Odoo (Inventory or Sales → Products) |
| `No journal of type "bank" found` | Configure a Bank journal in Odoo Accounting settings |
| `HTTP 404` | Verify `ODOO_URL` is reachable and Odoo is running |
| `current_balance: null` | Account has no default configured; check Odoo journal settings |
