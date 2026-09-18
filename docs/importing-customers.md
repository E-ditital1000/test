# Importing the customer list

One-time load of the existing customers, their sites and their contacts,
after Operations has cleaned the list. Start from
[customer-import-template.csv](customer-import-template.csv).

## Run it

```bash
# 1. Check. Writes nothing, however many times you run it.
python manage.py import_customers customers.csv

# 2. Read the report, fix the file, check again. Repeat until it is clean.

# 3. Save, recorded against the person responsible for the list.
python manage.py import_customers customers.csv --commit --as ops@a1.lr
```

On the VPS, prefix with `sudo -u a1360 /srv/a1360/.venv/bin/` and run from
`/srv/a1360`.

**It saves everything or nothing.** If any row has an error, nothing is
written, so there is never a half-loaded list with no way to tell which
half is missing.

## The columns

Only `name` is required. Headers can be in any case, with spaces or
underscores (`Contact Name` and `contact_name` are the same column).

| Column | What goes in it |
| --- | --- |
| `name` | The customer. **Required on every row.** |
| `phone`, `email`, `address`, `notes` | The customer's own details |
| `contact_name`, `contact_job_title`, `contact_phone`, `contact_email` | A person at the customer |
| `site_name`, `site_address` | A place work happens |
| `site_latitude`, `site_longitude` | Optional; both or neither, as decimals (`6.300000`, `-10.800000`) |

**A customer with several sites or contacts goes on several rows** with the
same name. The first row's phone, email and address are kept; if a later row
disagrees, the report says so. The first contact listed becomes the main
contact.

## What the report tells you

| Heading | Meaning | Blocks saving? |
| --- | --- | --- |
| **Errors** | A row that cannot go in as written: no name, a bad email, a coordinate that is not a number, a value too long | Yes |
| **Warnings** | Worth a look: an ignored column (often a typo in a header), two names sharing a phone number, conflicting details for one customer | No |
| **Skipped** | Already a customer, matched by name, phone or email | No |

A customer already in A1 360 is **never changed** by an import. That makes
it safe to run the same file again after fixing it: what went in last time
is skipped, not duplicated. Change an existing customer on its own screen.

Names match ignoring case, full stops and commas (`Acme Ltd.` is `ACME LTD`).
Phone numbers match on their last nine digits, so `+231 77 012 3456` and
`077 012 3456` are the same number.

## Saving from Excel

**File → Save As → CSV UTF-8**. Plain CSV, semicolon-separated files and
files with accented names all read correctly too.
