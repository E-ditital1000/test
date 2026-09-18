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
| Email notifications | **Yes.** Needs an email service added to the system (below). |
| Who may ask for work | **Only contacts marked as allowed to.** Every other login can see, not ask. |
| Scheduled maintenance | **Its own kind of work:** a contract with recurring visits (below). |

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

Only a contact marked **Can request work** (a new flag on `crm.Contact`,
off by default, set by staff) sees the request form. For everyone else it
is absent, not disabled, and the server refuses the request if it is posted
anyway. This lets a customer's accounts clerk follow invoices without being
able to commission work.

## Email notifications

The system sends no email today. Adding it is its own piece of work, and
it also unlocks things staff have wanted, such as a password reset link by
email.

**How it sends.** Every email is written to an **outbox** table first and
sent by a separate job (`manage.py send_outbox`, run every minute by a
systemd timer). A slow or unreachable mail server then never slows down or
breaks the screen that caused the email, a failed send is retried, and
there is a record of what was sent to whom and when.

**What it sends to customers:**

| Event | Sent to |
| --- | --- |
| A field visit is booked, moved or cancelled | Contacts at that customer |
| A project changes stage | Contacts at that customer |
| An invoice is issued | Contacts at that customer |
| A portal request is received, and when its status changes | The contact who raised it |
| A shared document or report is added | Contacts at that customer |
| Their portal login is created | That contact (instead of HR reading out a password) |

Each contact can switch each kind off from the portal. Every email says what
happened and links to the portal page; it never carries amounts, documents
or anything else that should stay behind the sign-in.

## Maintenance contracts

Scheduled maintenance is **its own kind of work**, not a ticket raised each
time.

**A contract** (new model) belongs to a customer and names: the site or
sites it covers, the service type, how often a visit is due (monthly,
quarterly, twice a year, yearly), a start date and an end or renewal date,
and who at A-1 owns it.

**Visits are created from it.** A daily job (`manage.py schedule_maintenance`)
creates each **field job** a set number of days before it is due, linked to
the contract. From then on it is an ordinary field job: assigned, visited,
assessed and approved exactly as today, so field crews learn nothing new. A
visit that is missed or pushed back does not shift the next one; the
schedule follows the contract, not the last visit.

**Everyone can see where it stands.**

- Staff: a contracts register with each contract's next due visit, visits
  overdue, and contracts coming up for renewal. Overdue maintenance visits
  join the attention queue.
- The customer, in the portal: their contracts, the visit history against
  each, and the next visit due. The next-visit reminder is one of their
  email notifications.

**New permissions:** `view_contracts`, `manage_contracts` (create, change,
end). The contract and its visits are audited like every other record.

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

## Suggested build order

1. **Email service and outbox.** Everything else sends through it.
2. **Portal sign-in, scoping and read-only screens**: projects, visits,
   shared documents, invoices and payments.
3. **Requests from the portal**, with the Can request work flag.
4. **Maintenance contracts and the visit scheduler**, then their portal view.
5. **Customer notifications**, event by event.

Each step ships on its own with its tests, so the portal can open to
customers after step 3 without waiting for contracts.

## Still open

- **Email provider and sender address.** An SMTP service (such as Postmark,
  Mailgun or Amazon SES) and a sending domain, e.g. `notifications@a1…`, with
  its SPF and DKIM records set so emails are not marked as spam. Somebody at
  A-1 needs to own the account.
- **How far ahead to create maintenance visits.** Suggested: 14 days before
  they are due, so operations can plan crews.
- **How maintenance is billed.** An invoice per visit, or a fixed amount per
  month or year under the contract? This decides whether contracts need
  their own invoicing rules.
- **Who approves a scheduled visit.** Does a created maintenance visit go
  straight onto the job list, or wait for a supervisor to confirm the date?
