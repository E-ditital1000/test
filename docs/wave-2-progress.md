# Wave 2 — complete

Wave 1 (contracts, auth, Settings, HR register) and the design-system pass
are in [wave-1-notes.md](wave-1-notes.md) and [design-system.md](design-system.md).

All five Wave 2 modules are built and the offline layer is complete.
**90 tests pass**, `manage.py check` is clean and there is no migration drift.

## What shipped

| Module | Screens |
| --- | --- |
| **Customers & Tickets** | Ticket list with filter chips and one-action assignment · intake form · ticket detail with activity log and **convert to project** · customer register · customer detail with sites, contacts and service history |
| **Projects** | List · detail with lifecycle stepper, tasks, crew, documents, requisitions and query-time cost |
| **Field Jobs** | Technician's job list · job detail · GPS check-in · **offline multi-step assessment** generated from the service type's question set · office scheduling screen |
| **Assessment review** | Review queue (oldest first) · three-column read-only review with approve / return · the shared append-only approval mechanism |
| **HR attendance** | Offline clock in/out with GPS · supervisor roll-call with exceptions first · event log with corrections · monthly report with CSV export |
| **Finance** | Invoices with lines and payments · expenses with receipt capture · requisition approval with the Settings-owned threshold |

## The guarantees, and where they are tested

- **One job, one identity.** `test_one_job_traced_from_first_call_to_paid_invoice`
  walks a call → ticket → project → field job → assessment → approval →
  invoice → payment and asserts a single `job_ref` survives every hop.
- **Offline is the design, not a fallback.** The assessment is filled in
  entirely on the device and reaches the server as one payload carrying the
  UUID it was created with. Re-submitting — after a dropped response, or from
  a second device — resolves to the row that exists. Clock events work the
  same way, with an IndexedDB queue that retries on `online`.
- **GPS never blocks an event.** Five seconds, then the event records marked
  `location_unavailable`. Asserted for both the clock and check-in.
- **Corrections supersede, never overwrite.** The original event row is
  untouched; a blank reason cannot submit.
- **Derived figures stay derived.** `AttendanceDay` is rebuilt from events;
  project cost and invoice paid-state are computed at query time. No stored
  totals.
- **Retiring a question never alters a recorded answer** — the question text
  is copied at submission.
- **Scope qualifies permission**, asserted for supervisors against another
  team's assessments and attendance.

## Four gaps this uncovered in Wave 1

1. **`close_ticket` was granted to no role but Admin.** Now Supervisor.
2. **No permission existed for scheduling a field job.** §5 gives Supervisors
   "assign jobs" but Wave 1 shipped no code, so the screens had nothing to
   gate on. Added `schedule_field_job` to the registry (which its own
   docstring sanctions for later waves) and granted it to Supervisor and
   Project Manager.
3. **Assessment review was granted at `all` scope**, so any supervisor could
   approve any crew's site work — while attendance was correctly scoped to
   own team. Both `review_assessment` and `approve_assessment` are now
   own-team, which is what the release gate tests for.
4. **The role matrix had stopped being a release gate.** `require_permission`
   now stamps its code on the view and the suite walks the URLconf, so a new
   gated screen missing from the matrix fails the build.

Also fixed: ticket assignment filtered on the HR register, leaving the
dropdown silently empty for a technician with a login but no HR record. It
now filters on who holds `view_own_job_list`.

## The offline layer

Both gaps left open at first pass are now closed.

**A service worker** at `/sw.js` — served from the root, because a worker's
scope is its own directory and one under `/static/` could not control the
app. It precaches the shell and serves pages network-first with a cache
fallback, so a technician who closes the app on site and reopens it gets
their job list rather than the browser's error page. Two rules it will not
break: it never caches a write (a clock event is replayed by the page, where
something can report the outcome), and it drops every cached page the moment
an unauthenticated page loads — which is what stops a shared phone showing
the last person's records. `/offline/` is the last resort when a screen has
never been opened on that device, and it says plainly that queued work is
safe.

**One shared offline layer** in `static/js/a1-offline.js`: `A1.uuid()`,
`A1.locate()` and `A1.store`. The clock, the check-in and the assessment had
each carried their own copy of the id generator and the GPS timeout; they now
share one, and a test fails the build if a template reimplements either.

**`A1.store` is IndexedDB**, with localStorage as a fallback. Assessment
drafts carry their photographs, and a handful of them exceeds the ~5 MB
localStorage ceiling — losing a technician's afternoon because the fifth
photo overflowed a quota is exactly the failure this system exists to
prevent. Photos are still downscaled to 1280 px before storage.

Also installable: `manifest.webmanifest` lets the phone add A1 360 to the
home screen and open it standalone.

## Still to come

**Reports (Agent 9)** is Wave 3, as planned — the fixed report set, the
executive command view and the dashboard attention queue.

## Running it

```
python manage.py migrate
python manage.py seed_permissions --reset-system-roles
python manage.py seed_demo_data
python manage.py test
```

Sign in at `/login/` — the demo panel lists one account per role
(development only, gated on `SHOW_DEMO_ACCOUNTS`).
