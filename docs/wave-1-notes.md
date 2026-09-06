# Wave 1 — Contracts & foundation: what shipped, what I assumed

Covers Agent 1 (data model & migrations) and Agent 2 (auth, roles &
Settings), plus the HR employee register and the permission-driven shell.

## What is done

| Deliverable | State |
| --- | --- |
| Frozen schema + migrations, all eight modules | Done — 10 apps, migrations applied |
| Permission list published as the contract | Done — 38 codes in `accounts/permission_registry.py` |
| Role-by-permission matrix document | Done — generated to `docs/permission-matrix.md` |
| Auth: email+password, session expiry, forced reset, lockout | Done |
| Nine pre-built roles, seeded as data | Done — `manage.py seed_permissions` |
| Admin-created custom roles, with guard rails | Done — Settings → Roles |
| Settings: users, service types + question sets, status lists, company, policy, audit log | Done |
| Permission-driven navigation and shell | Done |
| Seeded demo dataset | Done — `manage.py seed_demo_data` |
| HR employee register | Done |
| Attendance derivation + rebuild routine | Done — `hr/services.py` |

41 tests pass, including the two the brief names: every pre-built role
against every gated endpoint, and a custom role created at runtime reaching
exactly its grants.

## Running it

```
python manage.py migrate
python manage.py seed_permissions      # permissions + the nine roles
python manage.py seed_demo_data        # config + 12 employees, 6 customers, 10 tickets, 5 projects
python manage.py export_permission_matrix
python manage.py test
```

Seeded accounts use `firstname.lastname@a1edigital.test` and the password
passed to `seed_demo_data` (default `Demo!Pass123`); each is forced to
change it at first sign-in.

## Decisions I made, for the record

1. **"Admin" is defined by a permission, not a role name.** The last-admin
   guard rail counts users holding `manage_roles`, because an admin is free
   to rename the Admin role. Guarding the name would be trivially bypassed.
2. **Scope is applied explicitly at each call site.** There is no universal
   FK path from Customer, Project and Employee back to a user, so
   `apply_scope()` takes the `Q` that "own team" or "own projects" means for
   *that* model. A scoped view that forgets to call it returns nothing
   rather than everything.
3. **Assessment answers copy their question text at submission.** This is
   what makes "retiring a question must not alter historical answers" true
   even if the question is later reworded.
4. **Work is assigned to user accounts; HR data hangs off `hr.Employee`.**
   The permission layer therefore never depends on the HR module being
   present, and supervisor scoping resolves through `Employee.supervisor`.
5. **Module screens for Waves 2–3 are permission-gated placeholders.** The
   routes and the gates are real now — a technician typing `/finance/invoices/`
   gets 403 today. Only the screens are outstanding.
6. **`AttendanceDay` is a cache, not a source.** `rebuild_attendance_days()`
   is the only writer, and a test proves a wiped table reconstructs
   identically.

## One question for the CTO — not built, deliberately

§5 gives the Receptionist role "log calls", but there is **no call-logging
permission in the frozen permission list**, and Agent 3's brief (Customers &
Tickets) does not mention call logs. Per the rule that a gap in the brief
escalates rather than being invented, I have not built it and have not added
a permission code for it.

Two ways to close it, both cheap — but the choice is yours, and it needs to
land before the day-5 freeze:

- **Treat a call as a ticket.** Receptionists already hold `create_ticket`;
  no schema or permission change. This is my recommendation.
- **Add a `CallLog` model** with a `log_call` permission code, granted to
  Receptionist. Costs one permission, one table, one screen in Wave 2.

## Not built, per the deferred column

No payroll, salary, rate or bank field exists anywhere in HR — a test
asserts their absence. No SSO, no two-factor, no customer portal, no SLA
timers, no Gantt, no custom report builder.
