# Skill: Odoo MCP

## Purpose
Use the `odoo` MCP server to read and write Odoo 19 accounting and partner data from Claude — without leaving the terminal.

All write operations (invoices, payments) are created in **DRAFT** status. A human must open Odoo and confirm/validate before anything posts to the ledger.

## Trigger phrases
- "create an invoice for …"
- "draft an invoice to …"
- "list invoices" / "show unpaid invoices"
- "get invoice #…"
- "create a payment for invoice …"
- "what is the account balance?"
- "list recent transactions"
- "add a partner" / "create a customer"
- "look up partner …"

---

## Prerequisites

1. Odoo 19 running (use the `odoo_setup_skill` if needed).
2. `odoo-mcp` installed and registered in `~/.claude/mcp.json` (see below).
3. Vault `.env` contains `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD`.

---

## MCP registration

Add to `~/.claude/mcp.json` (merge with existing entries):

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

Install dependencies once:
```bash
cd /mnt/d/HACKATHON_00/AI_Employee_Vault/MCP_Servers/odoo-mcp
npm install
```

---

## Tool usage examples

### Create a customer invoice
```
create_invoice(
  partner_name = "Acme Corp",
  lines = [
    { product: "Consulting Services", quantity: 10, price: 150 },
    { product: "Travel Expenses",     quantity: 1,  price: 250 }
  ]
)
```
Returns: `invoice_id`, `reference`, `amount_total`, and a **DRAFT warning**.

### List all draft invoices
```
list_invoices(status = "draft")
```

### List invoices for a date range
```
list_invoices(date_from = "2026-01-01", date_to = "2026-03-31")
```

### Get a specific invoice with line details
```
get_invoice(invoice_id = 42)
```

### Create a bank payment for an invoice
```
create_payment(invoice_id = 42, amount = 1750.00, payment_method = "bank")
```
Returns: `payment_id`, `reference`, and a **DRAFT warning**.

### Check account balances
```
get_account_balance()
```
Returns current balance for every bank and cash journal.

### List recent transactions
```
list_transactions(date_from = "2026-04-01", date_to = "2026-04-10")
```

### Create a new partner
```
create_partner(name = "Jane Smith", email = "jane@example.com", phone = "+1-555-0100")
```
Returns the new (or existing) partner record.

---

## Safety rules

| Rule | Detail |
|------|--------|
| Never auto-post invoices | `action_post` is never called; state stays `draft` |
| Never auto-validate payments | `action_post` is never called; state stays `draft` |
| Human confirmation required | Open Odoo → find the record → click Confirm / Validate |
| Duplicate partner protection | `create_partner` checks email before creating; returns existing if found |

---

## Workflow: full invoice → payment cycle

1. **Create partner** (if new): `create_partner(...)`
2. **Create invoice** (draft): `create_invoice(...)`
3. **Human step**: open Odoo → find invoice → click **Confirm** → invoice moves to `posted`
4. **Create payment** (draft): `create_payment(invoice_id, amount, "bank")`
5. **Human step**: open Odoo → find payment → click **Validate** → payment posts and reconciles

---

## Files

| Path | Role |
|------|------|
| `MCP_Servers/odoo-mcp/index.js` | MCP server — all tool implementations |
| `MCP_Servers/odoo-mcp/package.json` | Node.js package manifest |
| `MCP_Servers/odoo-mcp/README.md` | Full technical reference |
| `Skills/odoo_mcp_skill/SKILL.md` | This file — usage guide |
