# Attendance column contract

The columns a Phase Two payroll engine consumes **unchanged**. Everything
here is derived from the append-only `hr.AttendanceEvent` log plus its
corrections — nothing is stored by hand, and re-running the derivation
reproduces every figure exactly.

Produced by `/hr/report/?export=csv`, implemented in `hr.views._monthly_csv`.
A test asserts the header line verbatim, so **changing a name here breaks a
build, deliberately** — it is a breaking change for the consumer.

## The columns

| Column | Type | Meaning |
| --- | --- | --- |
| `staff_id` | text | The employee's identifier on the HR register. Stable; survives deactivation. |
| `surname` | text | Register form, sorted on. |
| `first_name` | text | |
| `department` | text | Free text from the register; may be blank. |
| `days_present` | integer | Days with at least one clock-in, late days included. |
| `days_absent` | integer | Days in the period with a derived row and no clock-in. |
| `late_arrivals` | integer | Days whose first clock-in falls after the workday start plus the grace period. |
| `total_hours` | decimal, 2dp | Paired clock-in/clock-out durations, summed. An unpaired clock-in contributes nothing. |
| `corrections` | integer | Corrections applied to the events behind this month. |

## What decides "late"

Two Settings-owned values, not constants:

- `attendance_workday_start` — e.g. `08:00`
- `attendance_late_after_minutes` — the grace period, e.g. `15`

If the workday start is unset or unparsable, lateness is **not judged** and
every attended day is `present`. That is deliberate: a missing policy should
not silently mark a workforce late.

## What is deliberately absent

No pay rate, salary, wage, bank detail or leave balance exists anywhere in
the HR module — not in the export, not in the model, not as a nullable field
"for later". Phase One is attendance only. Payroll in Phase Two reads these
columns and holds its own money data.

`hr.tests` asserts the header contains none of `salary`, `rate`, `pay` or
`wage`.

## Edge cases the consumer should expect

- **Still clocked in.** An employee who never clocked out has a day row with
  `still_clocked_in` true and no paired duration, so it adds `0` to
  `total_hours` while still counting as present. The roll-call surfaces this
  as an exception for a supervisor to correct.
- **A corrected event** keeps its original row. The export uses the
  *effective* time — the correction's value — and counts the correction.
- **A voided event** drops out of every figure but stays on the record.
- **A deactivated employee** keeps every historical row. They fall out of the
  current month's report because the register filters to active staff; their
  past months still reconstruct in full.
