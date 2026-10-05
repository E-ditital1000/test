"""
Taking the existing staff on in one go, for cutover.

Each row goes through exactly what the Add employee screen does: the same
form checks it, and the same service creates the person, their sign-in and
their access together. So an import cannot create anybody the screen would
have refused, and cannot grant a role the person running it could not.

The temporary passwords are the one thing the screen shows and an import
cannot: there are dozens. They are written to a separate file for HR to
hand out, never printed, since a terminal's scrollback is often logged.
"""
import csv
import io
from datetime import date

from django.db import transaction

from accounts.models import Role
from accounts.services import assignable_roles
from config.csv_import import Report, read_rows

from .forms import EmployeeOnboardingForm
from .models import Department, Employee

REQUIRED = ["first_name", "last_name", "email", "staff_id"]
KNOWN = REQUIRED + ["phone", "job_title", "department", "supervisor", "start_date", "roles"]
DEFAULT_ROLE = "Employee"


def _parse_date(value):
    """
    ISO only. 3/9/2024 is the 3rd of September to half the office and the
    9th of March to the other half (and to Excel), and a wrong start date
    silently shifts somebody's attendance history.
    """
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, f"Start date '{value}' must be written as YYYY-MM-DD, e.g. 2024-09-03."


def plan(raw, *, actor):
    """
    Check the file without writing anything. Returns (report, people): each
    person is {"line", "data", "roles", "supervisor_ref"}.
    """
    report = Report()
    rows, unknown = read_rows(raw, required=REQUIRED, known=KNOWN)
    for header in unknown:
        report.warn(None, f"Column '{header}' is not one this import reads, so it is ignored.")

    grantable = {role.name.lower(): role for role in assignable_roles(actor)}
    all_roles = {role.name.lower(): role for role in Role.objects.all()}

    people, emails, staff_ids = [], {}, {}
    for row in rows:
        v, line = row.values, row.line
        problems_before = len(report.errors)

        # Roles: names separated by | or ; -- a comma would split the column.
        names = [n.strip() for n in v.get("roles", "").replace(";", "|").split("|") if n.strip()]
        if not names:
            names = [DEFAULT_ROLE]
        roles = []
        for name in names:
            role = grantable.get(name.lower())
            if role:
                roles.append(role)
            elif name.lower() in all_roles:
                report.error(line, f"You cannot grant '{name}': it holds permissions you do not hold yourself.")
            else:
                report.error(line, f"There is no role called '{name}'.")

        # A spreadsheet carries a department's name, and the form now wants
        # a row from the list. At cutover that spreadsheet IS where the list
        # comes from, so an unknown name creates one — and says so, because a
        # typo would otherwise become a department of one person for ever.
        department = None
        raw_department = " ".join(v.get("department", "").split())
        if raw_department:
            department = Department.objects.filter(name__iexact=raw_department).first()
            if department is None:
                department = Department.objects.create(name=raw_department)
                report.warn(line, f"Added '{raw_department}' to the department list.")

        start = None
        if v.get("start_date"):
            start, problem = _parse_date(v["start_date"])
            if problem:
                report.error(line, problem)

        form = EmployeeOnboardingForm(
            data={
                "first_name": v.get("first_name", ""),
                "last_name": v.get("last_name", ""),
                "email": v.get("email", ""),
                "phone": v.get("phone", ""),
                "staff_id": v.get("staff_id", ""),
                "job_title": v.get("job_title", ""),
                "department": department.pk if department else "",
            },
            actor=actor,
        )
        # Roles, supervisor and start date are checked above and below, in
        # the terms a spreadsheet uses (names and emails, not database ids).
        for name in ("roles", "supervisor", "start_date"):
            form.fields[name].required = False
        if not form.is_valid():
            for field, errors in form.errors.items():
                if field in ("roles", "supervisor", "start_date"):
                    continue
                label = form.fields[field].label if field in form.fields else field
                for error in errors:
                    report.error(line, f"{label}: {error}")

        email = v.get("email", "").strip().lower()
        staff_id = v.get("staff_id", "").strip()
        if email and email in emails:
            report.error(line, f"Email {email} is also on line {emails[email]}.")
        if staff_id and staff_id.lower() in staff_ids:
            report.error(line, f"Staff ID {staff_id} is also on line {staff_ids[staff_id.lower()]}.")
        emails.setdefault(email, line)
        staff_ids.setdefault(staff_id.lower(), line)

        if len(report.errors) > problems_before:
            continue
        data = dict(form.cleaned_data)
        data["start_date"] = start
        data["supervisor"] = None
        data["reason"] = f"Imported from a staff list, line {line}"
        people.append({
            "line": line,
            "data": data,
            "roles": roles,
            "supervisor_ref": v.get("supervisor", "").strip(),
        })

    # Supervisors: by email or staff ID, already on the register or earlier
    # or later in this same file. Resolved after every row is read so the
    # order of the file does not matter.
    in_file = {}
    for person in people:
        in_file[person["data"]["email"].lower()] = person
        in_file[person["data"]["staff_id"].lower()] = person
    for person in people:
        ref = person["supervisor_ref"].lower()
        if not ref:
            continue
        if ref in in_file:
            if in_file[ref] is person:
                report.error(person["line"], "Somebody cannot be their own supervisor.")
            continue
        existing = Employee.objects.filter(is_active=True).filter(
            user__email__iexact=ref
        ).first() or Employee.objects.filter(is_active=True, staff_id__iexact=ref).first()
        if existing is None:
            report.error(
                person["line"],
                f"Supervisor '{person['supervisor_ref']}' is not an email or staff ID of anyone "
                "on the register or in this file.",
            )
        else:
            person["data"]["supervisor"] = existing

    failed = {line for line, _m in report.errors}
    people = [p for p in people if p["line"] not in failed]
    for person in people:
        d = person["data"]
        report.created.append(
            "{} {} <{}> {} as {}".format(
                d["first_name"], d["last_name"], d["email"], d["staff_id"],
                ", ".join(r.name for r in person["roles"]),
            )
        )
    return report, people


def import_staff(raw, *, actor, commit=False):
    """
    Plan, and only if `commit` and nothing is wrong, create everybody in one
    transaction. Returns (report, passwords): passwords is [(employee,
    temporary)] and is empty unless something was saved.
    """
    from . import services

    report, people = plan(raw, actor=actor)
    if not commit or not report.ok or not people:
        return report, []

    passwords = []
    with transaction.atomic():
        created = {}
        for person in people:
            employee, temporary = services.onboard_employee(
                actor=actor, data=person["data"], roles=person["roles"]
            )
            passwords.append((employee, temporary))
            created[employee.user.email.lower()] = employee
            created[employee.staff_id.lower()] = employee
        # Second pass: supervisors who were themselves in the file.
        for (employee, _t), person in zip(passwords, people):
            ref = person["supervisor_ref"].lower()
            if ref and person["data"]["supervisor"] is None and ref in created:
                employee.supervisor = created[ref]
                employee.save(update_fields=["supervisor"])
    report.committed = True
    return report, passwords


def passwords_csv(passwords):
    """The sheet HR hands out from, then destroys."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["name", "email", "staff_id", "temporary_password"])
    for employee, temporary in passwords:
        writer.writerow([employee.full_name, employee.user.email, employee.staff_id, temporary])
    # BOM so Excel opens it as UTF-8 and accented names survive.
    return "﻿" + out.getvalue()
