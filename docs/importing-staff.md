# Taking the existing staff on in one go

For cutover. Each row does exactly what **HR → Employees → Add employee**
does: the person's record, their sign-in and their roles, together or not
at all. Start from [staff-import-template.csv](staff-import-template.csv).

## Run it

```bash
# 1. Check. Writes nothing.
python manage.py import_staff staff.csv --as hr@a1.lr

# 2. Fix the file until the check is clean.

# 3. Save. The temporary passwords go into the file you name.
python manage.py import_staff staff.csv --as hr@a1.lr --commit --passwords-out passwords.csv
```

`--as` is the person taking them on. It is needed even for a check, because
which roles you may grant depends on who you are: exactly as on the screen,
**you can only grant roles whose permissions you hold yourself**. An HR user
can take people on as Employee or HR; granting Technician needs an Admin or
Supervisor to run the import (or to add the role afterwards).

**Everybody or nobody.** One bad row and nothing is saved.

## The passwords sheet

Every new person gets a temporary password. The import writes them to the
`--passwords-out` file and **never prints them**, because terminal history
is often logged.

- Hand each password over in person.
- Each person must choose their own at first sign-in, so the sheet stops
  working as they do.
- **Delete the file once they are all handed out.**

The import refuses to overwrite an existing file, since an earlier sheet
might hold passwords nobody has handed out yet.

## The columns

| Column | What goes in it | Required |
| --- | --- | --- |
| `first_name`, `last_name` | | Yes |
| `email` | Their work email. It is what they sign in with. | Yes |
| `staff_id` | | Yes |
| `phone`, `job_title`, `department` | | No |
| `supervisor` | The supervisor's **email or staff ID**. They can be on the register already or anywhere in the same file. | No |
| `start_date` | **YYYY-MM-DD** only, e.g. `2024-09-03` | No |
| `roles` | Role names separated by `\|`, e.g. `Technician\|Employee`. Empty means **Employee**. | No |

Dates are ISO only because `03/09/2024` is 3 September to some people and
9 March to others (and to Excel), and a wrong start date shifts somebody's
attendance history. In Excel, format the column as text or as a custom
`yyyy-mm-dd` date before saving.

## What it refuses

Everything the Add employee screen refuses, from the same checks: an email
already signing in, a staff ID already on the register, a missing name. It
also refuses the same email or staff ID twice in the file, a supervisor it
cannot find, somebody supervising themselves, an unknown role name, and a
role you are not allowed to grant.
