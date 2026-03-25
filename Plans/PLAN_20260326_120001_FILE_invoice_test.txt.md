---
source_file: FILE_invoice_test.txt.md
created: 2026-03-26T12:00:01Z
type: file_drop
priority: medium
autonomy: needs_approval
status: in_progress
---

## Objective
Review the dropped file `invoice_test.txt` and obtain human approval before taking any financial action.

## Steps
- [ ] Step 1 — Open and read the full contents of `invoice_test.txt` to determine if this is a real invoice or a test artifact
- [ ] Step 2 — If test artifact: confirm with human whether to discard or archive
- [ ] Step 3 — If real invoice: extract amount, payee, and due date; compare against Business_Goals.md thresholds
- [ ] Step 4 — Present findings to human for sign-off decision (process, discard, or archive)
- [ ] Step 5 — Log outcome and move to Done/ after approval

## Notes
Autonomy escalated to **needs_approval** because the filename contains the keyword "invoice" — per Company_Handbook.md rule: "flag if filename suggests invoice/contract/legal" for file_drop types.
The "test" suffix is ambiguous: could be a development/test file or a genuine invoice labelled "test". Human judgement required to clarify intent.
