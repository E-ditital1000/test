# Taking somebody on

One screen: **HR → Employees → Add employee**.

It creates three things together, or none of them:

1. **The person's record** on the employee register — staff ID, job title,
   department, supervisor, start date.
2. **Their sign-in** — the work email is the username. A temporary password
   is generated and shown **once**, on the confirmation message.
3. **Their access** — the roles ticked decide every permission they hold.

Give the temporary password to them directly. They are forced to replace it
at first sign-in, so after that nobody but them knows it.

## What changed, and why

It used to be three steps across two modules: create the account in
Settings → Users, create the employee record in HR → Employees, then go back
and grant a role. The middle step demanded an account only Settings could
make — so **the HR person whose job this is could not finish it**, and it
fell to an Admin every time.

It also left half-made people around. An account with no employee record
cannot clock in. An employee record with no account cannot sign in. Whoever
was interrupted had no way to tell which they were looking at.

Now it is one transaction: all three exist afterwards, or none do.

## Why some roles are not offered

The list only shows roles whose permissions **you already hold**.

This matters because the screen shows the new password. Without the rule, an
HR user could create an Admin account and immediately sign in as it — taking
somebody on would be a way around every other permission in the system.

Creating a role was always guarded this way. **Assigning** one was not, which
left the guard trivial to walk around, and that is now closed everywhere —
in the Settings screen too, not just here.

So in practice:

| Who is taking them on | Can grant |
| --- | --- |
| Admin | Any role |
| HR | Employee, HR |

**Most people should be taken on as `Employee` anyway** — the floor every
staff account starts on: clock in and out, and see their own attendance.

Field or finance access is granted afterwards, in **Settings → Users**, by
whoever owns that work. A Technician can submit assessments and see costs
against jobs; that is an operational decision, not an HR one.

If you would rather HR could take technicians on directly, that is a policy
change, not a code one — see the open question below.

## Editing afterwards

**HR → Employees → Edit** changes the job details: staff ID, title,
department, supervisor, whether they still work here.

It does **not** offer the sign-in account. An employee record stays bound to
the account it was created with, because repointing it would silently move
that person's entire attendance history onto somebody else.

To change what somebody can reach, use **Settings → Users**.

## Deactivating

**HR → Employees → Deactivate.** The record and every attendance event it
owns are kept — deactivation is not deletion. They stop appearing on the
roll-call and in the current month's report; past months still reconstruct
in full.

## An open question for the CTO

The rule above means HR cannot take a technician on in one step: the
Technician role holds `view_own_job_list`, `gps_check_in` and
`submit_assessment`, none of which HR holds.

That is deliberate and safe, but it is a policy choice worth confirming.
Three options:

1. **Leave it.** HR takes people on at Employee level; a Supervisor or Admin
   grants Technician afterwards. Two people, two decisions — which is
   arguably right, since HR does not own field operations.
2. **Widen the rule** so a role is assignable when it grants nothing from
   the `settings` module — HR could then grant Technician and Supervisor,
   but never Admin. Usable, and still closes the escalation path.
3. **Add a permission** such as `grant_operational_roles`, given to HR, and
   list explicitly which roles it covers.

My recommendation is **(1) for the pilot** — it is the safest and matches
"Employee, the floor every staff account starts on" from the plan — and to
revisit it if onboarding volume makes the second step a real nuisance.
