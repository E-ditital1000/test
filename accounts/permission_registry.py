"""
The Wave 1 permission contract. Every module builds against these codes —
`@require_permission("code")` call sites are checked against this list at
startup (see accounts.checks), and `seed_permissions` writes this list into
the Permission table. Add codes here as later waves need them; never add a
code only in a decorator call and never enforce access by string-comparing
a role name instead of checking a permission code.

Each entry: (code, module, description).
"""

PERMISSIONS = [
    # Settings
    ("manage_users", "settings", "Create, edit and deactivate user accounts"),
    ("manage_roles", "settings", "Create roles and grant permissions to them"),
    ("manage_service_types", "settings", "Manage service types and their assessment question sets"),
    ("manage_status_lists", "settings", "Manage ticket/project status option lists"),
    ("manage_company_details", "settings", "Edit company details shown across the system"),
    ("view_audit_log", "settings", "View the system audit log"),

    # Dashboard
    ("view_dashboard", "dashboard", "View the role-aware dashboard landing screen"),
    ("view_executive_dashboard", "dashboard", "View the executive command view"),

    # Customers & Tickets
    ("view_customers", "crm", "View the customer register"),
    ("create_customer", "crm", "Create a new customer record"),
    ("create_ticket", "crm", "Create a new ticket"),
    ("assign_ticket", "crm", "Assign a ticket to a technician"),
    ("view_ticket_status", "crm", "View ticket status and history"),
    ("close_ticket", "crm", "Close a ticket"),
    ("convert_ticket_to_project", "crm", "Convert a ticket into a project"),

    # Projects
    ("view_projects", "projects", "View project records"),
    ("manage_project", "projects", "Manage a project's tasks, crew and lifecycle stage"),
    ("view_project_cost", "projects", "View a project's running cost and revenue"),
    ("raise_requisition", "projects", "Raise a requisition against a project"),

    # Field Jobs (mobile)
    # Added in Wave 2: someone has to be able to put a job on a technician's
    # phone. Section 5 gives Supervisors "assign jobs" but Wave 1 shipped no
    # code for it, so the screens had nothing to gate on.
    ("schedule_field_job", "fieldjobs", "Schedule a field job and assign it to a technician"),
    ("view_own_job_list", "fieldjobs", "View own assigned field job list"),
    ("gps_check_in", "fieldjobs", "GPS check in on arrival at a field job"),
    ("submit_assessment", "fieldjobs", "Submit a structured site assessment"),

    # Assessment review & approvals
    ("review_assessment", "approvals", "Review a submitted assessment"),
    ("approve_assessment", "approvals", "Approve or return a submitted assessment"),

    # Finance
    ("view_invoices", "finance", "View invoices and payment status"),
    ("issue_invoice", "finance", "Create and send an invoice"),
    ("record_payment", "finance", "Record a payment against an invoice"),
    ("log_expense", "finance", "Log an expense against a project"),
    ("approve_requisition", "finance", "Approve a requisition (subject to the configured threshold)"),
    ("view_financial_reports", "finance", "View financial reports"),

    # Reports
    ("view_reports", "reports", "View the fixed operational report set"),
    ("export_reports", "reports", "Export report data"),

    # HR
    ("view_employees", "hr", "View the employee register"),
    ("manage_employees", "hr", "Create, edit and deactivate employee records"),
    ("view_attendance_records", "hr", "View attendance records"),
    ("correct_attendance", "hr", "Correct an attendance event, with a mandatory reason"),
    ("view_attendance_reports", "hr", "View the monthly attendance report"),
    ("clock_in_out", "hr", "Clock in and out, and view own attendance history"),
]

PERMISSION_CODES = {code for code, _module, _description in PERMISSIONS}
