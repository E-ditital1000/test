# Who can say a piece of work is finished

A-1 reported this as one line: *"Technicians can't click complete task in
the system. Both field jobs, project or ticket."*

It was three separate things wearing the same symptom, and the rule that
resolves all three is already in the codebase, in `projects.views.task_toggle`:

> Whoever the task belongs to can finish it. Requiring `manage_project`
> would mean a technician cannot tick off their own work, which is the
> surest way to have a task list nobody keeps up to date.

This note records how that rule now reads across the three kinds of work,
and why each one draws its line where it does.

## Task

**Whoever it is assigned to, and whoever runs the project.** Unchanged — it
always worked.

What was missing was the button. The toggle lived on the project page and
on the field job, and was absent from both screens a technician actually
opens: **My work**, which claims in its own subtitle to hold *everything
handed to you*, and **Today's jobs**. A route nothing offers is a route
nobody uses.

Both now carry the action, and both pass `next` so finishing a task returns
you to the list you finished it from. Without that, a technician ticking a
task off their dashboard was sent to the project page — which they may not
be able to open at all, since `view_projects` is scoped to own projects and
they are crewed on none of them.

## Field job

**The lead, or the office** (`schedule_field_job` / `manage_records`).

Before this there was no such action at all. The only thing that ever moved
a visit to Completed was an assessment arriving, so a visit producing no
assessment — a delivery, a part fitted, a return trip for a fuse — stayed
*In progress* for ever and the board's "Not finished" count only grew.

The lead, for the same reason the lead submits the assessment: one person
is accountable for the visit. A crew member is told whose job it is rather
than refused silently. The office can close one too, because a lead who has
gone home sick should not hold the board open.

**A visit completed by its assessment is not reopened here.** The assessment
is the record of what happened on site; reopening the visit would invite a
second one. Returning the assessment from the review queue is how that is
undone, and the refusal says so.

## Ticket

**Whoever holds `close_ticket`, or the person it was assigned to.**

A technician could open the ticket handed to them — that was fixed on the
first sheet — and there was nothing on the screen to say the work was done.
Worse, the *Status & priority* card rendered for everybody while its route
asks for `view_ticket_status`, so the one control a technician was offered
answered 403. A button that does nothing is worse than no button: it tells
somebody their account is broken.

So:

- the assignee gets a **Mark finished** form, with an optional note of what
  was done, which lands in the activity log;
- the office is **emailed** when a technician closes one, because they are
  the people who answer the customer;
- the status card is **gated** on the permission its own route requires;
- **Reopen** exists, held by `close_ticket`. It had to: the moment the
  person doing the work can close a ticket, a customer who rings back the
  next day leaves the office with a record it cannot touch.

Closing stays reversible and fully attributed rather than being made
harder. The activity log says who pressed it, and reopening is an event of
its own — so the history shows a ticket closed and reopened, not a ticket
that quietly never closed.

## What was deliberately not done

**Technicians were not given `close_ticket`.** That permission is scoped
`all` and would let any employee close any ticket in the company. The right
being granted here is narrower and is checked against the record, not the
role: *this one is yours*.

**The status list was not opened to them either.** Moving a ticket to "In
progress" is a reasonable thing for a technician to want and a separate
question from finishing it. Adding it would mean deciding whether they may
also change priority, which is the office's call. Left alone until A-1 ask.
