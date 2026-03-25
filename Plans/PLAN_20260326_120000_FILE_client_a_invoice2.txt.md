---
source_file: FILE_client_a_invoice2.txt.md
created: 2026-03-26T12:00:00Z
type: file_drop
priority: medium
autonomy: needs_approval
status: in_progress
---

## Objective
Review the dropped invoice file `client_a_invoice2.txt` and obtain human approval before taking any financial action.

## Steps
- [ ] Step 1 — Open and read the full contents of `client_a_invoice2.txt` to extract invoice amount, recipient, and due date
- [ ] Step 2 — Cross-check invoice amount against Business_Goals.md financial thresholds ($100 flag limit)
- [ ] Step 3 — Verify recipient is a known/recurring payee; flag as new payee if not found in records
- [ ] Step 4 — Present summary to human for approval: amount, payee, due date, and recommended action
- [ ] Step 5 — Upon approval, log payment action and move to Done/

## Notes
Autonomy escalated to **needs_approval** because the filename contains the keyword "invoice" — per Company_Handbook.md rule: "flag if filename suggests invoice/contract/legal" for file_drop types.
Financial rules require human approval for all payments over $100 and all new recipients.
Actual invoice amount unknown until file contents are read by an authorised human.
