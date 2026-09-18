# Customer portal — plan for Phase Two

**Status: agreed, not built.** Scheduled as the first Phase Two item, after
the Phase One pilot and cutover. It was deliberately left out of Phase One
([wave-1-notes.md](wave-1-notes.md)), and it is the first time people outside
A-1 get into the system, so it waits until the system has run live with
staff only.

## What a customer can do

Sign in and, **for their own company only**:

- See every project they have with A-1: its stage, the stage history, and
  what is scheduled next.
- See the field visits on those projects: when, and whether they are done.
- Read documents and approved assessments that staff have **marked to
  share**.
- See their **invoices and payments**, and what is outstanding.
- **Ask for new work or maintenance** from the portal.

## Decisions taken

| Question | Decision |
| --- | --- |
| When | After the Phase One pilot; first item in Phase Two |
| Money | Invoices and payments only. Never A-1's internal costs, expenses, requisitions, rates or profit. |
| Documents and reports | Only those staff tick **Share with customer**. Nothing is shared by default. |

## How it fits the system as built

**Who signs in.** A portal login belongs to a person at the customer: a
`crm.Contact`. Staff invite them from the customer's page; they get a
temporary password (the same one-time flow as staff onboarding) and must
choose their own at first sign-in. A customer can have several logins, one
per contact, and each is ended by deactivating it, like any account.

**What they hold.** A new **Customer** role carrying *no staff permission at
all*. Every staff screen is already refused server-side to anyone without
its permission code, so a customer typing a staff URL gets a 403 with no new
code. The permission matrix test is extended to prove it for every gated
screen.

**Where they go.** Their own screens under `/portal/`, in their own shell:
no sidebar of staff modules, no search across the company, no attention
queue. A portal user who reaches a staff page is sent to `/portal/`.

**What they see.** Every portal query starts from one function that returns
the signed-in contact's customer and filters by it. There is no pk in any
portal URL that is not checked against that customer, and a test asks every
portal URL for another customer's record and expects a 404, not a 403: a
403 would confirm the record exists.

**Sharing.** A `shared_with_customer` flag on `ProjectDocument` and on
`Assessment` (approved ones only), off by default, set by staff on the
existing screens. Internal notes, crew names beyond the lead, GPS
check-ins, costs and requisitions are never in a portal view, flagged or
not.

**Asking for work.** A portal request creates a **Ticket**, marked as raised
through the portal and attached to the customer and, optionally, one of
their sites. It lands in the reception and operations queue that already
exists, so nobody learns a new inbox. The customer sees their requests and
each one's status.

## Security checklist before it ships

- [ ] Every portal view scoped through the one customer filter; tested with
      two customers against each other.
- [ ] Every staff URL refuses a portal user (matrix test extended).
- [ ] No internal field reaches a portal template: a test renders each portal
      page and asserts known internal fields are absent.
- [ ] Portal sign-ins and requests are in the audit log.
- [ ] Rate limit on portal sign-in (lockout already exists) and on request
      submission.
- [ ] The HTTPS deployment is live; never over plain HTTP.

## Still open

- **Notifications.** Should a customer get an email when a visit is booked,
  a stage changes or an invoice is issued? Needs an email service, which the
  system does not have yet.
- **Who may request work.** Every portal login, or only a contact marked as
  able to?
- **Maintenance contracts.** Is scheduled maintenance its own kind of job
  (recurring visits against a contract), or a ticket like any other? A
  recurring schedule is a larger piece of work.
