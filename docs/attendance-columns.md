# Attendance column contract

The columns a Phase Two payroll engine consumes **unchanged**. Everything
here is derived from the append-only `hr.AttendanceEvent` log plus its
corrections **and from approved leave** — nothing is stored by hand, and
re-running the derivation reproduces every figure exactly.

> **Changed with leave management.** Three columns were added and
> `days_present` narrowed. This is a breaking change for the consumer, taken
> deliberately: before it, payroll could not tell a paid leave day from an
> unexplained absence. See [leave-management-plan.md](leave-management-plan.md).

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
| `days_present` | integer | Days somebody **worked**: state `present` or `late`. **Narrowed** — this used to mean "not absent", which would have counted paid leave as attendance once a fourth state existed. |
| `days_absent` | integer | Days with a derived row, no clock-in, **and no approved leave**. An absence explained by approved leave is not an absence. |
| `late_arrivals` | integer | Days whose first clock-in falls after the workday start plus the grace period. |
| `days_on_leave_paid` | integer | **New.** Days covered by approved leave of a type marked paid, where nobody clocked. |
| `days_on_leave_unpaid` | integer | **New.** The same for a leave type marked unpaid. |
| `worked_while_on_leave` | integer | **New.** Days approved leave covered *and* somebody clocked. Paid as worked, counted in `days_present`, and **not** in either leave column — surfaced so HR can decide whether the leave day goes back on the balance. |
| `total_hours` | decimal, 2dp | Paired clock-in/clock-out durations, summed. An unpaired clock-in contributes nothing. |
| `corrections` | integer | Corrections applied to the events behind this month. |

## What decides "late"

Two Settings-owned values, not constants:

- `attendance_workday_start` — e.g. `08:00`
- `attendance_late_after_minutes` — the grace period, e.g. `15`

If the workday start is unset or unparsable, lateness is **not judged** and
every attended day is `present`. That is deliberate: a missing policy should
not silently mark a workforce late.

## Leave, and what is still deliberately absent

Leave is now tracked, so these columns carry it. **No leave balance is
stored anywhere**: entitlement comes from the bands in `hr.LeaveType` and
length of service, days taken come from approved requests, and the
subtraction happens when somebody asks. `AttendanceDay.leave_type` is a
derived cache like every other column here — it exists so this export can
tell paid from unpaid without joining back to the request.

No pay rate, salary, wage or bank detail exists anywhere in the HR module —
not in the export, not in the model, not as a nullable field "for later".
Payroll in Phase Two reads these columns and holds its own money data; what
a leave day is *worth* is its problem, not this one.

`hr.tests` asserts the header contains none of `salary`, `rate`, `pay` or
`wage`, and asserts the full header line verbatim.

## Where a clock event was taken

Every clock event has always carried a GPS fix. Until now it was compared to
nothing: there was nowhere in the system to say what "here" meant, so
somebody could clock in from home and the record would faithfully store the
coordinates of their living room.

HR or an Admin now sets one office location and a radius in
**HR → Where people clock**. Attendance codes carry no coordinates of their
own — a code stays a code.

| Situation | What happens |
| --- | --- |
| Inside the radius | Recorded, `location_check = at_the_place` |
| Outside it, but within the phone's own stated accuracy | Recorded, `location_check = near_enough`, flagged on the roll-call |
| Outside both | **Refused.** No event is written |
| No fix, attendance code scanned | Recorded, `location_check = not_checked` |
| No fix, no code | **Refused** |
| No office location set | Recorded, nothing judged |
| A supervisor recording on somebody's behalf | Never judged |

Two of those are the whole design rather than details.

**A scanned code does not excuse a bad position.** `AttendanceCode` says of
itself that a printed code is a shared secret and a photograph works as well
as the paper. The code proves possession; the fix is what proves presence.

**No fix without a code is refused.** Otherwise denying the browser's
location permission is simply how somebody clocks in from home, and the
whole check is theatre. With a code, possession stands in for position —
which is what keeps a phone that cannot see satellites inside a concrete
building from stopping a real employee working.

`distance_m` is stored on the event, not recomputed. The office location is
a setting somebody can change, and recomputing would silently restate where
people were on days nobody revisited — the same reason a leave request
stores its own day count.

Neither field is exported. Payroll is owed hours, not positions; the
columns above are unchanged by this.

## Edge cases the consumer should expect

- **Never clocked out.** An employee who went home without clocking out has a
  day row with `still_clocked_in` true, no `last_out`, and no paired
  duration, so it adds `0` to `total_hours` while still counting as present.

  **The day closes with the day and cannot be reopened.** They may not come
  back and clock out against it later: the system never saw them leave, so
  any end it accepted would be a time somebody chose rather than a time
  anybody worked, and those hours would reach payroll as fact. Settling what
  was actually worked is a supervisor correction, which carries an actor and
  a mandatory reason — a late clock-out would carry neither.

  The consequence, stated rather than hidden: a shift running past midnight
  cannot be clocked out of the next morning. On A-1's six-day week ending in
  the evening that is the right trade, and it is theirs to revisit if night
  work becomes normal.
- **A corrected event** keeps its original row. The export uses the
  *effective* time — the correction's value — and counts the correction.
- **A voided event** drops out of every figure but stays on the record.
- **A deactivated employee** keeps every historical row. They fall out of the
  current month's report because the register filters to active staff; their
  past months still reconstruct in full.
- **Approved leave needs no clock event to appear.** Somebody away for a
  month files nothing, so nothing triggers a rebuild for their days. The
  roll-call and this report therefore read approved leave *directly* through
  `hr.services.approved_leave_days` as well as through the stored rows — one
  function, three callers, so the table and the screens cannot disagree.
- **Future leave is never in this table.** `AttendanceDay` describes days
  that have happened. Leave approved for next March lives on the leave
  calendar until the day arrives.
- **Cancelled leave restates the days.** Cancelling approved leave rebuilds
  the affected rows, so days that had been accounted for go back to being
  absences unless something else explains them.
