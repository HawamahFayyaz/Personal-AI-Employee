# Skill: CEO Monday Briefing

## Purpose

Generate a comprehensive Monday morning briefing for the CEO. It is the
showcase output of the AI Employee — a single document that replaces an
hour of manual data gathering and gives a data-driven picture of the past
week, revenue health, operational bottlenecks, and proactive suggestions.

Invoked every Sunday evening by `scripts/generate_briefing.py`, which
pre-collects all vault data and passes it as a structured JSON packet.
You must **analyse that packet** and produce the briefing — do not re-gather
data from files unless a specific field says `"has_data": false`.

---

## Trigger phrases

- "Generate the weekly briefing"
- "Create the Monday CEO briefing"
- "Run the CEO briefing skill"
- "What happened this week?"
- Any invocation by `orchestrator.py` or `scheduler.py` using prompt key `ceo_briefing`
- Any invocation by `scripts/generate_briefing.py`

---

## Input: Data Packet Schema

The script passes a JSON object with these top-level keys. Understand each
before writing the briefing.

```
period
  .start              ISO date — first day of the reporting period
  .end                ISO date — last day of the reporting period
  .report_date        ISO date — the Monday this briefing is dated
  .days               int — number of days in the period

business_goals
  .revenue_target_monthly   int | null  — monthly revenue goal ($)
  .revenue_mtd              int | null  — MTD revenue from Business_Goals.md
  .kpis[]                   list of {metric, target, alert_threshold}
  .subscription_rules[]     audit criteria from Business_Goals.md
  .raw_available            bool

completed_tasks
  .total                    int
  .by_priority              {high, medium, low, critical} → count
  .by_type                  {file_drop, approval_request, …} → count
  .by_outcome               {approved, rejected, completed, …} → count
  .items[]                  list of task summaries:
    .name                   filename
    .type                   task type
    .priority               high | medium | low | critical
    .outcome                approved | rejected | completed | …
    .turnaround_hours       float | null
    .resolved_date          YYYY-MM-DD

bottlenecks
  .total                    int
  .items[]                  tasks that exceeded their SLA:
    .task                   filename
    .priority               level
    .expected_hours         float (from SLA table below)
    .actual_hours           float
    .delay_hours            float

log_activity
  .days_with_logs           int
  .total_entries            int
  .success_count            int
  .error_count              int
  .dry_run_count            int
  .by_action_type           {action_type → count}
  .orchestrator_runs        int
  .reasoning_loops          int
  .watcher_errors[]         recent ERROR lines from watcher logs
  .summaries[]              recent reasoning-loop summaries

accounting
  .has_data                 bool — false if no Accounting/ file exists
  .source                   relative path to file | null
  .revenue_this_week        float | null
  .revenue_mtd              float | null
  .expenses_mtd             float | null
  .raw_excerpt              first 800 chars of the accounting file | null

social
  .has_data                 bool
  .platforms[]              ["facebook", "instagram", "twitter", …]
  .summaries[]              list of {file, platform, date, excerpt}

pending
  .approvals_count          int — items in Pending_Approval/
  .needs_action_count       int — items in Needs_Action/
  .inbox_count              int — items in Inbox/
  .oldest_approval_days     int | null
  .oldest_approval_name     str | null

vault_snapshot
  .briefings_total          int
  .done_total               int
  .plans_active             int
```

---

## Analysis Phase

Work through these checks before writing the briefing. Record results for
each section in working memory — do not write until you have analysed all.

### A — Revenue Health

1. Determine `revenue_mtd` from `accounting.revenue_mtd` (preferred) or
   `business_goals.revenue_mtd` (fallback).
2. Calculate `pct_of_target = revenue_mtd / (revenue_target_monthly / 4 * weeks_elapsed) * 100`
   — compare against the current week in the month.
3. Assign trend:
   - **Ahead** if MTD is > 5% above pro-rated target
   - **On track** if within ±5% of pro-rated target
   - **Behind** if 5–20% below pro-rated target
   - **Critical** if > 20% below pro-rated target
4. Check each KPI in `business_goals.kpis` — flag any where the actual value
   (if knowable from the data) exceeds the `alert_threshold`.

### B — Operational Health

1. **Approval queue age**: if `pending.oldest_approval_days > 1` (> 24 hours),
   flag it as a stale approval with the item name.
2. **Error rate**: if `log_activity.error_count > 0`, list errors from
   `log_activity.watcher_errors`.
3. **Dry-run-only days**: if `log_activity.dry_run_count > 0` and
   `log_activity.success_count == 0`, the system ran in DRY_RUN all week.

### C — Bottlenecks

SLA thresholds from Company_Handbook.md priority levels:

| Priority | SLA |
|----------|-----|
| critical | 15 min (0.25 h) |
| high     | 1 hour |
| medium   | 4 hours |
| low      | 24 hours |

Use `bottlenecks.items` — they are pre-calculated. If `total == 0`, write
"No SLA breaches this week." in the table placeholder.

### D — Subscription / Cost Flags

1. List `business_goals.subscription_rules` as the active audit criteria.
2. If no live subscription data is in the packet, flag:
   > ⚠️ No subscription usage data available — manual audit recommended.
3. If `accounting.expenses_mtd` is set and exceeds the `> $600/month` alert
   threshold from Business_Goals.md, flag it.

### E — Social Media

1. If `social.has_data == false`, note "No social data for this period."
2. Otherwise, parse the `social.summaries[].excerpt` markdown for metric
   tables and include the headline numbers.

### F — Proactive Suggestions

Generate at least 2–3 actionable items. Pull from:
- Bottlenecks detected in phase C
- Stale approvals from phase B
- Revenue gap from phase A
- Any reasoning-loop summaries mentioning flags or blockers
- Read `Plans/` to identify approaching deadlines (files with `deadline:` in
  frontmatter, or plans created > 5 days ago with status still `pending`)

---

## Output Format

Write the briefing **exactly** in this format. Do not add, remove, or rename
sections. Fill every section — use "N/A" or the appropriate message if data
is unavailable; never leave a section blank or delete it.

```markdown
---
generated: <ISO 8601 timestamp with timezone>
period: <YYYY-MM-DD to YYYY-MM-DD>
briefing_type: monday_ceo
source: AI Employee v1.0
---

# Monday Morning CEO Briefing — <report_date formatted as "Month DD, YYYY">

## Executive Summary

<2–3 sentences. Cover: revenue vs target, operational highlights, top risk or
win. Be direct. No filler. Do not repeat section headings — synthesise.>

## Revenue

| Period | Amount | Target | % of Target | Trend |
|--------|--------|--------|-------------|-------|
| This Week | $X | $Y/wk | Z% | On track / Behind / Ahead / Critical |
| Month-to-Date | $A | $B | C% | On track / Behind / Ahead / Critical |

<If revenue data is unavailable: "> ⚠️ No accounting data found.
> To enable revenue tracking, create `Accounting/Current_Month.md` with
> revenue and expense figures, or configure the Odoo integration.">

**KPI Alerts:**
<list any KPIs exceeding alert_threshold, or "_All KPIs within thresholds._">

## Completed Tasks (<N> total)

<list each task as a checkbox, grouped by outcome, most recent first.
Format: `- [x] FILENAME — outcome in Y.Zh`
If none: "_No tasks completed this period._">

## Bottlenecks

| Task | Priority | Expected | Actual | Delay |
|------|----------|----------|--------|-------|
<one row per bottleneck item, or "_No SLA breaches this week._" below the header row>

## System Health

- **Approval Queue**: <N> items pending<if oldest > 1 day: ` — ⚠️ oldest: NAME (D days)`>
- **Needs Action**: <N> items
- **Inbox**: <N> items
- **Orchestrator Runs**: <N> this week (<success> succeeded, <error> errors)
- **Log Errors**: <N> error lines<if errors: list up to 5>
<if dry_run_count > 0 and success == 0: "- ⚠️ System ran in DRY_RUN mode all week — no real API calls were made.">

## Social Media

<If has_data: list platform headlines from summaries.
 If not: "_No social media data for this period. Configure Meta/Twitter watchers to enable._">

## Proactive Suggestions

### Cost Optimization

<subscription flags, expense threshold breaches, or manual-audit note if no data>

### Upcoming Deadlines

<list plans with deadline or created > 5 days ago and still pending; read Plans/ folder.
 If none: "_No upcoming deadlines identified._">

### Operational Improvements

<1–3 specific, actionable recommendations derived from the data — not generic advice.
 Each should reference a specific data point (e.g. "FILE_X took 6h vs 1h SLA").>

---
*Generated by AI Employee v1.0 — {period_start} to {period_end}*
```

---

## Dashboard Update

After writing the briefing file, update `Dashboard.md` with two changes:

### 1. Latest Briefing section

Find or create `## Latest Briefing` immediately after `## Alerts`.
Replace its content with:

```markdown
## Latest Briefing

- [`<Month DD, YYYY> Monday Briefing`](Briefings/<filename>) — generated <YYYY-MM-DD HH:MM UTC>
```

### 2. Recent Activity entry

Prepend to the `## Recent Activity` list:

```
- `<YYYY-MM-DD HH:MM>` — [CEO Briefing] <report_date> briefing generated → <filename>
```

---

## Logging

After the briefing is written, append one entry to the daily JSON log at
`Logs/YYYY-MM-DD.json` (same format as all other vault log entries):

```json
{
  "timestamp": "<ISO>",
  "action_type": "ceo_briefing",
  "result": "success",
  "output_file": "<filename>",
  "period": "<start> to <end>",
  "summary": "Monday CEO briefing generated for <report_date>"
}
```

---

## Quality checklist

Before finishing, verify:

- [ ] All 8 top-level sections present in the briefing
- [ ] Revenue table has both rows (or the ⚠️ unavailability message)
- [ ] Bottlenecks table has a row or the "no breaches" message
- [ ] Executive Summary does not just list sections — it synthesises
- [ ] Every Proactive Suggestion cites a specific data point
- [ ] Dashboard.md updated with Latest Briefing link
- [ ] JSON log entry written
- [ ] Printed: `BRIEFING COMPLETE → Briefings/<filename>`

---

## Quick-reference: SLA thresholds

| Priority | Max turnaround |
|----------|---------------|
| critical | 15 min |
| high     | 1 hour |
| medium   | 4 hours |
| low      | 24 hours |

## Quick-reference: Revenue trend rules

| Condition | Trend label |
|-----------|-------------|
| MTD ≥ pro-rated target × 1.05 | **Ahead** |
| MTD within ±5% of pro-rated target | **On track** |
| MTD 5–20% below pro-rated target | **Behind** |
| MTD > 20% below pro-rated target | **Critical** |
