# Wave 3 — complete

Waves 1 and 2 are in [wave-1-notes.md](wave-1-notes.md) and
[wave-2-progress.md](wave-2-progress.md); the design system is in
[design-system.md](design-system.md).

**113 tests pass**, `manage.py check` is clean, no migration drift, and the
acceptance gate passes.

## What shipped

| Surface | What it is |
| --- | --- |
| **Attention queue** | One list of everything blocked on you, ranked by how long it has waited — not grouped by type. Each row links straight to the record. |
| **Executive command view** | Active projects, open tickets, pending approvals, on-time completion, jobs completed per month, and the queue. Every figure counted from rows at request time. |
| **Reports** | Six fixed reports — jobs completed, ticket ageing, project profitability, revenue vs. expense, technician output, attendance summary — each with CSV export. |
| **Acceptance gate** | `manage.py acceptance` runs the scenarios the plan names and reports pass/fail, exiting non-zero on any failure. |

## The report set is a set, not a builder

`reports/reports.py` is a registry. Each entry names its own permission, its
columns and the query that produces its rows, so adding a report is a
deliberate act with an owner and no user can compose one that reaches data
their role does not.

Three properties are tested rather than asserted in prose:

- **Every report tallies against its raw rows.** Each report's figures are
  recomputed independently in the test and compared. A report that disagrees
  with the records underneath it is worse than no report, because somebody
  will act on it.
- **Totals come from the rendered rows**, not a second query, so a report
  cannot disagree with itself.
- **The export is the same rows in the same order** as the screen.

Per-report permissions hold server-side: a role with `view_reports` but not
`view_financial_reports` sees the operational reports and is **refused**
project profitability, not merely shown a hidden link. Reading a report and
taking it away are separate permissions (`view_reports` / `export_reports`).

HR deliberately cannot reach the Reports module: the operational reports
carry customer names, and §5 denies HR customer data. HR has its own monthly
attendance report at `/hr/report/`.

## The attention queue

Its contract is narrow and worth defending: **everything blocked on this
person, ranked by wait, and nothing they cannot act on.** A row somebody
cannot clear is noise in a list whose entire value is that every row is
actionable.

The sharpest case: a requisition above the approval threshold does **not**
appear in a Finance queue, because Finance cannot clear it — it appears in an
Executive's. Tested both ways.

Everything is gathered under the permission that governs it and through the
same scoping the module uses, so the queue can never surface a record its
reader could not open.

## The release gate

```
python manage.py acceptance
```

Prints the scenario list, runs them, and reports `GATE: PASS` or
`GATE: FAIL` with the failing scenario named. Exits non-zero on failure, so
CI can hold a deploy on it. Verified by deliberately breaking the lineage
assertion: the gate failed, exited 1, and named the test.

The 31 tagged scenarios live beside the code they test, so they are
maintained by whoever changes the behaviour rather than by whoever
remembers.

**A pass is necessary, not sufficient.** The gate prints three checks that
can only be proven on a device, and they are part of it:

- [ ] Two-tap clock-in under ten seconds on a low-end Android, timed
- [ ] A full assessment on a low-end Android in airplane mode, then synced
- [ ] Legible in sunlight, usable one-handed, on the device the crews carry

## Smaller fixes

- `seed_permissions` ignored `--verbosity 0`, so every test fixture flooded
  the console. It honours it now.
- The gate's own output is ASCII: Windows consoles default to cp1252 and a
  box-drawing character crashed it mid-report. A release gate must not fail
  on its own printing.

## What is left before cutover

Everything in the plan's §6 build is now done. Outstanding from §6 and §8,
none of which is code:

- **Customer data load** — an explicit W3 deliverable with a cleaning pass
  owned by Operations.
- **Training material.**
- **Parallel pilot, days 13–15** — one crew and one office desk running live
  alongside the current method.
- **The three device checks above.**

One engineering note for the pilot: the service worker needs **HTTPS**
(localhost excepted). If the pilot box is served over plain HTTP, offline
page loads silently will not engage.
