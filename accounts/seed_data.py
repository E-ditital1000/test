"""
Pre-built roles: starting points an Admin can copy or amend in Settings, not
a fixed ceiling. Each entry maps a role name to a list of (permission_code,
scope) grants, matching the role table in the Phase One brief section 5.
"""

from .models import SCOPE_ALL, SCOPE_OWN_PROJECTS, SCOPE_OWN_TEAM
from .permission_registry import PERMISSION_CODES

ALL_CODES = sorted(PERMISSION_CODES)

ROLE_GRANTS = {
    # Everything, including creating roles, granting permissions and
    # reading the audit log.
    "Admin": [(code, SCOPE_ALL) for code in ALL_CODES],

    # Read everything; approve above the requisition threshold; the
    # command view.
    "Executive": [
        ("view_dashboard", SCOPE_ALL),
        ("view_quotations", SCOPE_ALL),
        ("view_executive_dashboard", SCOPE_ALL),
        ("view_customers", SCOPE_ALL),
        ("view_ticket_status", SCOPE_ALL),
        ("view_projects", SCOPE_ALL),
        ("view_project_cost", SCOPE_ALL),
        ("view_field_jobs", SCOPE_ALL),
        ("review_assessment", SCOPE_ALL),
        ("view_invoices", SCOPE_ALL),
        ("approve_requisition", SCOPE_ALL),
        ("view_financial_reports", SCOPE_ALL),
        ("view_reports", SCOPE_ALL),
        ("export_reports", SCOPE_ALL),
        ("view_employees", SCOPE_ALL),
        ("view_attendance_records", SCOPE_ALL),
        ("view_attendance_reports", SCOPE_ALL),
        ("clock_in_out", SCOPE_ALL),
    ],

    # Invoices, payments, expenses, requisition approval, project cost,
    # financial reports.
    "Finance": [
        ("view_dashboard", SCOPE_ALL),
        ("view_projects", SCOPE_ALL),
        ("view_project_cost", SCOPE_ALL),
        ("view_quotations", SCOPE_ALL),
        ("manage_quotations", SCOPE_ALL),
        ("view_invoices", SCOPE_ALL),
        ("issue_invoice", SCOPE_ALL),
        ("record_payment", SCOPE_ALL),
        ("log_expense", SCOPE_ALL),
        ("approve_requisition", SCOPE_ALL),
        ("view_financial_reports", SCOPE_ALL),
        ("view_reports", SCOPE_ALL),
        ("export_reports", SCOPE_ALL),
        ("clock_in_out", SCOPE_ALL),
    ],

    # Employee register, attendance records, corrections, attendance
    # reports. No access to finance or customer data.
    "HR": [
        ("view_dashboard", SCOPE_ALL),
        ("view_employees", SCOPE_ALL),
        ("manage_employees", SCOPE_ALL),
        ("view_attendance_records", SCOPE_ALL),
        ("correct_attendance", SCOPE_ALL),
        ("view_attendance_reports", SCOPE_ALL),
        ("manage_attendance_codes", SCOPE_ALL),
        ("clock_in_out", SCOPE_ALL),
    ],

    # Create customers and tickets, log calls, view ticket status. No
    # costs, no approvals, no employee data.
    "Receptionist": [
        ("view_dashboard", SCOPE_ALL),
        ("view_customers", SCOPE_ALL),
        ("create_customer", SCOPE_ALL),
        ("create_ticket", SCOPE_ALL),
        ("view_ticket_status", SCOPE_ALL),
        ("clock_in_out", SCOPE_ALL),
    ],

    # Assign jobs, review and approve assessments, daily roll-call,
    # correct attendance for their own team.
    "Supervisor": [
        ("view_dashboard", SCOPE_ALL),
        ("view_customers", SCOPE_ALL),
        ("view_ticket_status", SCOPE_ALL),
        ("assign_ticket", SCOPE_ALL),
        # Someone has to be able to end the lifecycle the ticket list shows.
        # A Supervisor runs the work, so it lands with them by default; an
        # Admin can move it like any other grant.
        ("close_ticket", SCOPE_ALL),
        ("schedule_field_job", SCOPE_ALL),
        ("view_field_jobs", SCOPE_ALL),
        ("view_projects", SCOPE_ALL),
        # Own team, like attendance below it. A Supervisor signing off
        # another crew's site work is not what section 5 describes, and the
        # release gate tests for exactly that refusal.
        ("review_assessment", SCOPE_OWN_TEAM),
        ("approve_assessment", SCOPE_OWN_TEAM),
        ("view_attendance_records", SCOPE_OWN_TEAM),
        ("correct_attendance", SCOPE_OWN_TEAM),
        ("clock_in_out", SCOPE_ALL),
    ],

    # Own projects, tasks and crews; raise requisitions; view their
    # projects' costs.
    "Project Manager": [
        ("view_dashboard", SCOPE_ALL),
        ("view_customers", SCOPE_ALL),
        ("view_ticket_status", SCOPE_ALL),
        ("convert_ticket_to_project", SCOPE_ALL),
        ("schedule_field_job", SCOPE_ALL),
        ("view_field_jobs", SCOPE_ALL),
        ("view_projects", SCOPE_OWN_PROJECTS),
        ("manage_project", SCOPE_OWN_PROJECTS),
        ("view_project_cost", SCOPE_OWN_PROJECTS),
        ("raise_requisition", SCOPE_OWN_PROJECTS),
        ("clock_in_out", SCOPE_ALL),
    ],

    # Own job list, GPS check-in, submit assessments, own attendance
    # record. Nothing else.
    "Technician": [
        ("view_dashboard", SCOPE_ALL),
        ("view_own_job_list", SCOPE_ALL),
        ("gps_check_in", SCOPE_ALL),
        ("submit_assessment", SCOPE_ALL),
        # The projects they are actually on, and no others — the same rule
        # their job list follows. Without this a technician could raise a
        # requisition against a project (below) but never open it, and the
        # work they were sent to do had no context they could reach.
        # Scope does the limiting: "own projects" is the ones they are crewed
        # on or manage, so the list is theirs whoever else is on it.
        ("view_projects", SCOPE_OWN_PROJECTS),
        # What the job needs is known on the site and nowhere else. The
        # screen only reaches jobs they are on, and it commits nothing:
        # approval is still Finance's, above the threshold an Executive's.
        ("raise_requisition", SCOPE_OWN_PROJECTS),
        ("clock_in_out", SCOPE_ALL),
    ],

    # Clock in and out, view own attendance history. The floor every
    # staff account starts on.
    "Employee": [
        ("view_dashboard", SCOPE_ALL),
        ("clock_in_out", SCOPE_ALL),
    ],
}

SYSTEM_ROLE_NAMES = set(ROLE_GRANTS)
