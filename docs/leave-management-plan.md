# Leave management — assessment and plan

**Status: built. A-1 took the four decisions below on 11 October 2026 and
they are implemented as described.**

| Decision | A-1's answer | What it changed |
| --- | --- | --- |
| Working week | **Monday to Saturday** | Only Sunday is a rest day, so a statutory week of leave is **six** working days, not five. Annual leave tops out at 24 days, not 20. Stored as `leave_rest_days` in Settings. |
| Entitlement | **The statutory bands** | Seeded as 6 / 12 / 18 / 24 days at 0 / 12 / 36 / 60 months. Raisable in Settings without a release. |
| Who approves | **HR only** | Supervisors do *not* decide. They hold `view_leave` at own-team scope — enough to schedule around, no pen. This is simpler than the two-step chain originally proposed and removes the "who approves the approver" problem entirely. |
| Opening position | **Records too poor to reconstruct** | No opening balances are imported. Leave is tracked from a stated go-live date, and HR can correct any individual's figure with `LeaveAdjustment`, which carries a mandatory reason and an actor. |

Client item #6 from the handwritten feedback list: "leave management
(medical/other)". It is the only item on that list that is a new module
rather than a fix, and the only one that changes a contract another part of
the system already depends on.

This document says what must exist, what it touches, what it breaks, and
what A-1 has to decide first.

## Why this is not a form with a date range

Three things make leave structural rather than cosmetic.

**One: an approved leave day currently reads as absent.** The roll-call and
the monthly report derive every figure from the clock-event log. A day with
no clock-in has no derived row at all, and `hr.services.roll_call` treats a
missing row as `ABSENT`. So today, a person on approved annual leave appears
in the supervisor's exceptions list next to the person who simply did not
turn up. Building a leave form without fixing that produces a module that is
worse than nothing: it records approvals that the attendance screens
contradict.

**Two: leave is the one HR fact that other modules must consult.** A
supervisor scheduling a field job, assigning a task or assigning a ticket is
choosing a person for a date. That is exactly the question leave answers,
and it is the operational failure this module exists to prevent — somebody
dispatched to Zwedru on a day their leave was already approved.

**Three: the payroll contract says leave does not exist.**
[attendance-columns.md](attendance-columns.md) states that no leave balance
exists anywhere in HR, "not in the export, not in the model, not as a
nullable field for later", and `hr.tests` asserts the CSV header verbatim.
That was the right call for Phase One. Building leave means amending that
contract deliberately, with the test updated in the same commit.

## The statutory floor in Liberia

The module's defaults should start at the law and let A-1 be more generous,
never less. Under the **Decent Work Act of 2015**:

| | Entitlement |
| --- | --- |
| **Annual leave** | Accrues by tenure: 1 week in the first 12 months, 2 weeks after 12 months, 3 weeks after 36 months, 4 weeks (20 working days) after 60 months. Qualifying period is 12 months' continuous service with the same employer. |
| **Sick leave** | 10 days at full pay. A maximum of **3 unverified** sick days in any 12-month period — beyond that, medical documentation is required. |
| **Maternity leave** | A minimum of **14 weeks** paid, of which at least 6 must fall immediately after the birth. |
| **Public holidays** | 11 under the Patriotic Observances Law. Weekly rest days and public holidays are **not part of annual leave**, and nobody may be compelled to work a public holiday. |

Two consequences for the design, both non-negotiable:

- **Annual entitlement is a function of tenure, not a number.** It belongs in
  a settings-owned rules table, not a constant and not a single field on the
  employee.
- **Day counting must exclude weekends and public holidays.** A request from
  Thursday to the following Tuesday is not six days. That means the system
  needs to know Liberia's public holidays, and several of them move
  (Decoration Day is the second Wednesday of March, Fast and Prayer Day the
  second Friday of April). Storing real dates per year that HR confirms
  annually is honest; encoding "second Wednesday" rules is clever and will be
  wrong the first year a holiday is moved by proclamation.

Paternity leave appears in Liberian press coverage but I could not confirm
its current statutory standing. It is included as a **configurable leave type
with no assumed statutory minimum** — if A-1 grants it, they set the days.

## How it fits the system as built

Leave goes in the existing `hr` app, not a new one. It is about employees and
attendance, and the HR module nav already exists to hang it on.

### Models

**`LeaveType`** — settings-owned, exactly like `Department` and
`CorrectionReason`, for the same reason those are lists rather than text
boxes. Carries: code, label, whether it is paid, whether it needs a
supporting document, how many days may go unverified, whether it accrues by
tenure or is a flat grant, and whether it may go negative. Seeded with
Annual, Sick, Maternity, Paternity, Compassionate, Unpaid.

**`LeaveEntitlementRule`** — `(leave_type, after_months, days)`. Annual gets
four rows carrying the statutory bands. One row is a flat allowance. This is
how the law lives in data: when A-1 becomes more generous, or the Act is
amended, it is a Settings edit and not a release.

**`PublicHoliday`** — `(date, name)`, settings-owned, entered per year. The
11 statutory days for the current and next year are seeded; HR confirms them
each December.

**`LeaveRequest`** — employee, type, start and end date, reason, optional
supporting document, handover note, contact-while-away, and who submitted it
(HR can record on somebody's behalf, the same way a supervisor records a
clock event for a crew member).

Its **state is not a field.** `LeaveRequest` mixes in
`approvals.ApprovalTrailMixin`, so its state is the latest append-only
decision on it, carrying actor, comment and timestamp — the one approval
mechanism assessments and requisitions already use. A returned request must
carry a comment; the model refuses it otherwise. No new approval machinery is
invented, and the leave queue reads like the assessment queue because it is
the same thing.

**Balances are derived, never stored.** This follows `AttendanceDay`: a
mutable balance column is the field that silently goes wrong and that nobody
can reconstruct. Entitlement comes from the rules table plus tenure; days
taken come from approved requests; remaining is the subtraction, computed at
query time. If a report ever needs it cached, it is cached the way
`AttendanceDay` is — as a wipeable derivation with a test proving a rebuild
reproduces it.

**`LeaveOpeningBalance`** — the one stored number, and only because the
company does not start from zero. A-1 has staff who have already taken leave
this year. Without an actor-stamped opening figure per employee per leave
year, year one's numbers are wrong and nobody trusts the module afterwards.
This is entered by HR, not computed, and it is visible as an entered figure
rather than disguised as history.

### Permissions

Six new codes in the frozen registry, following the existing naming:

| Code | Who, by default |
| --- | --- |
| `request_leave` | **Every role** — the floor every staff account starts on, like `clock_in_out` |
| `view_leave` | Supervisor (own team), HR, Executive (all) |
| `approve_leave` | **HR and Admin only** — A-1's decision |
| `manage_leave` | HR — record, amend or cancel on somebody's behalf |
| `manage_leave_types` | HR, Admin — types, entitlement rules, public holidays |
| `view_leave_reports` | HR, Executive |

A supervisor's `view_leave` at `SCOPE_OWN_TEAM` mirrors their attendance
grant exactly, and a test asserts they are refused another team's request
and refused the decision on their own team's. The grants reach a live
database through `accounts/migrations/0008_leave_permissions.py` rather than
through `seed_permissions`, which deliberately leaves an existing role's
grants alone — without that migration the screens would ship live and
reachable by nobody.

### Notifications

Through `config/notifications.py`, with recipients read from the permission
and not from a role name. Two templates: `leave_requested.txt` to whoever may
decide it, `leave_decided.txt` to the employee, carrying the approver's
comment when it is returned.

**Deliberately not** an email to every administrator. The clock-event
notification just shipped sends 26 messages a day to every admin, and the
lesson recorded there applies in advance here: leave decisions go to the two
people involved, and "who is off this week" belongs on the roll-call screen,
not in an inbox.

## The attendance change

This is the part that carries risk, so it is specified rather than left to
the implementation.

`AttendanceDay` gains a fourth state, **`ON_LEAVE`**, and
`rebuild_attendance_days` becomes a pure function of *the event log plus
approved leave* rather than the event log alone. The docstring on
`hr.models.AttendanceDay`, the module docstring on `hr.services`, and
`attendance-columns.md` are all amended in the same commit to say so — the
existing wording promises something that will no longer be true, and a stale
invariant in a docstring is worse than none.

Keeping one derivation is the right trade even though it widens that promise.
The alternative — overlaying leave at display time in `roll_call` and
`monthly_summary` separately — reintroduces precisely the bug this codebase
keeps finding: the same rule living on one screen only, so the second screen
disagrees with the first.

Two rules inside it:

- **Rows are created for approved leave up to today, never for future leave.**
  `AttendanceDay` describes days that have happened. Future approved leave is
  visible on the leave calendar, which is the screen that exists to show it.
- **A clock-in on an approved leave day wins, and is flagged.** Somebody
  recalled from leave, or who came in anyway, worked — the event log is the
  record and it is not discarded. The day reads as present with a "worked
  while on leave" exception for HR, and whether that credits the leave day
  back is a policy question (below), not a silent default.

The CSV export gains **`days_on_leave_paid`** and **`days_on_leave_unpaid`**.
This is a breaking change to a contract a test asserts verbatim — which is
the mechanism working as designed, since payroll must be able to tell a paid
leave day from an absence. The doc and the test change together.

## The scheduling change

The highest-value part of the module, and the cheapest.

One shared helper — `hr.services.on_leave_between(start, end)` returning
`{user_id: LeaveRequest}` — consulted by the field job form, the task form
and the ticket assignment form.

It **marks, it does not filter.** The field job form already establishes this
pattern for project crew: people not on the project are labelled rather than
removed, because "a supervisor sending an extra body to a site is ordinary,
and a form that refuses it would be worked around by not using the form."
The same reasoning holds harder here. A technician can be recalled from
leave, and a scheduler who cannot see the person they need will schedule them
outside the system. So the picker reads **"Moses Toe — on leave 12–16 Oct"**,
and saving it warns rather than refuses.

## Screens

Added to the HR module nav, which exists because attendance was once "real,
working and effectively hidden" and the client reported being unable to find
it. Leave must not repeat that.

| Screen | Who | What it is for |
| --- | --- | --- |
| `/hr/leave/` | anyone with `request_leave` | My leave: balance, history, request button |
| `/hr/leave/new/` | same | The request form |
| `/hr/leave/requests/` | `view_leave` | The queue — waiting first, oldest at the top, like the assessment queue |
| `/hr/leave/<pk>/` | `view_leave` | One request, with the decision panel for `approve_leave` |
| `/hr/leave/calendar/` | `view_leave` | **Who is off, by month and department.** The screen that prevents double-booking |
| `/hr/leave/balances/` | `view_leave_reports` | Entitlement, taken, remaining, per employee |
| Settings | `manage_leave_types` | Types, entitlement rules, public holidays |

## What is deliberately absent

- **No money.** No leave encashment, no payment in lieu, no salary deduction
  for unpaid leave. Phase One HR holds no pay data and this does not change
  that; payroll in Phase Two reads the two new columns and holds its own
  money.
- **No TOIL or overtime banking.** A different mechanism with a different
  policy; conflating it with leave makes both unclear.
- **No approval chain beyond two steps.** `approvals/models.py` says "no
  approval chains beyond supervisor then executive" and leave does not get an
  exception.
- **No fractional monthly accrual** unless A-1 asks for it. The statutory
  bands are tenure thresholds, and modelling 1.67 days a month invents
  precision the law does not require.

## The four decisions, as answered

Kept here because the reasoning still matters: these are the figures
somebody will query in a year's time.

**1. Is Saturday a working day?** — **Answered: yes, Monday to Saturday.**
So a Thursday-to-Tuesday request costs five days, and the statutory four
weeks is 24 days. One caveat worth putting to a labour lawyer before a
dispute: the Act speaks in *weeks*, and the secondary sources gloss four
weeks as "20 working days", which assumes a five-day week. Reading a week as
six working days is the consistent interpretation on A-1's roster and is the
more generous one, so it is what is seeded.

**2. Statutory bands, or a flat company allowance?** — **Answered: the
statutory bands.** Seeded as 6 / 12 / 18 / 24 days. A flat allowance remains
expressible as a single band at zero months if A-1 ever changes its mind.

**3. Who approves, and who approves the approver?** Default is the employee's
supervisor from the register, then HR. Two gaps: a supervisor's own leave goes
to *their* supervisor, but the person at the top of the chain has nobody — and
long absences may warrant a second signature. Requisitions already escalate
to an Executive above a configured threshold, and leave can use the same
mechanism (e.g. over 10 days needs Executive sign-off) if A-1 wants it.

**4. What is the opening position on go-live?** Either A-1 provides days
already taken per employee for the current leave year, or the module starts
clean from a stated date and everything before it is outside the system. Both
are defensible; silently assuming zero is not, because it hands every
employee a full fresh allowance.

Also note: **tenure-based entitlement needs `Employee.start_date`, which is
nullable and blank on some records.** An employee with no start date will show
"needs a start date" rather than a wrong figure or a zero. Filling those in is
a data task for HR before go-live, and the balances screen will list who is
missing one.

### Assumptions taken where no answer is needed yet

Stated here so they are visible and correctable, rather than buried in code:

- **Full days only in version one.** Half-day leave adds a fraction to every
  balance, report and export column. Worth adding once the module is in use
  and only if people ask for it.
- **The leave year is the calendar year.** If A-1 runs an anniversary-based
  year instead, it is a settings value, not a rewrite.
- **Sick leave follows the statute:** 3 days without documentation in a
  rolling 12 months, documentation required beyond that, enforced as a
  `LeaveType` setting rather than code.
- **Carry-over is off by default.** Unused days expire at the leave year end.
  A per-type "may carry over, up to N days" setting exists but is unset.
- **A clock-in during approved leave does not automatically credit the day
  back.** It raises an exception for HR to decide, because the two cases —
  recalled to work, versus came in unasked — have different answers and the
  system cannot tell them apart.

## What was built

All three phases are in. What follows is what each one covered.

**Phase A — the spine.** `LeaveType`, `LeaveEntitlementRule`,
`PublicHoliday`, `LeaveRequest` on the shared approval trail, the six
permissions and their role grants, working-day counting, the request form and
the approval queue. At the end of this phase leave can be requested, decided
and seen, and nothing else in the system knows about it yet.

**Phase B — the integrations.** The `ON_LEAVE` attendance state, the amended
derivation and its documentation, the two export columns, and
`on_leave_between` wired into the three assignment forms. This is the phase
that changes existing behaviour, and it carries the regression risk; it ships
with the attendance rebuild test extended rather than relaxed.

**Phase C — the numbers.** Opening balances, the balances screen, the leave
calendar, and the Settings screens for types, rules and holidays.

Phase A is useful alone. Phase B is where the module starts preventing the
mistake it exists to prevent. Phase C is what makes it trustworthy at the end
of the first year.

## Sources

The statutory figures above are from secondary sources and should be
confirmed against the Act itself before they are seeded as policy:

- [Annual Leave, Holiday Pay, Weekly Rest Days — Liberia (WageIndicator)](https://wageindicator.org/en-lr/work-in-liberia/labour-law/annual-leave-and-holidays)
- [Sick Leave, Sick Pay, Job Security — Liberia (WageIndicator)](https://wageindicator.org/en-lr/work-in-liberia/labour-law/sick-leave/)
- [Decent Work Check Liberia 2025 (PDF)](https://wageindicator.org/media/documents/decentworkcheck-liberia-20250707-english-aa572e750395.pdf)
- [Annual Leave, Sick Leave and Public Holidays in Liberia: Payroll Guide (Paymaster Liberia)](https://paymasterliberia.com/blog/annual-leave-public-holidays-liberia-payroll)
