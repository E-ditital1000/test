# Permission list and role matrix

**Generated — do not edit by hand.** Run `python manage.py export_permission_matrix` after changing `accounts/permission_registry.py` or `accounts/seed_data.py`.

A role is a named bundle of permissions. Every screen and every endpoint asks *does this user hold this permission*, never *is this user a supervisor*. The roles below are the pre-built starting points an Admin can copy or amend in Settings — they are not a fixed set, and a custom role created at runtime is enforced exactly like these.

Scope: ✅ all · 👥 own team · 📁 own projects · blank = not granted.

| Permission | Module | Admin | Executive | Finance | HR | Receptionist | Supervisor | Project Manager | Technician | Employee |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `manage_users` | settings | ✅ |  |  |  |  |  |  |  |  |
| `manage_roles` | settings | ✅ |  |  |  |  |  |  |  |  |
| `manage_service_types` | settings | ✅ |  |  |  |  |  |  |  |  |
| `manage_status_lists` | settings | ✅ |  |  |  |  |  |  |  |  |
| `manage_company_details` | settings | ✅ |  |  |  |  |  |  |  |  |
| `view_audit_log` | settings | ✅ |  |  |  |  |  |  |  |  |
| `view_dashboard` | dashboard | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `view_executive_dashboard` | dashboard | ✅ | ✅ |  |  |  |  |  |  |  |
| `view_customers` | crm | ✅ | ✅ |  |  | ✅ | ✅ | ✅ |  |  |
| `create_customer` | crm | ✅ |  |  |  | ✅ |  |  |  |  |
| `create_ticket` | crm | ✅ |  |  |  | ✅ |  |  |  |  |
| `assign_ticket` | crm | ✅ |  |  |  |  | ✅ |  |  |  |
| `view_ticket_status` | crm | ✅ | ✅ |  |  | ✅ | ✅ | ✅ |  |  |
| `close_ticket` | crm | ✅ |  |  |  |  |  |  |  |  |
| `convert_ticket_to_project` | crm | ✅ |  |  |  |  |  | ✅ |  |  |
| `view_projects` | projects | ✅ | ✅ | ✅ |  |  | ✅ | 📁 |  |  |
| `manage_project` | projects | ✅ |  |  |  |  |  | 📁 |  |  |
| `view_project_cost` | projects | ✅ | ✅ | ✅ |  |  |  | 📁 |  |  |
| `raise_requisition` | projects | ✅ |  |  |  |  |  | 📁 |  |  |
| `view_own_job_list` | fieldjobs | ✅ |  |  |  |  |  |  | ✅ |  |
| `gps_check_in` | fieldjobs | ✅ |  |  |  |  |  |  | ✅ |  |
| `submit_assessment` | fieldjobs | ✅ |  |  |  |  |  |  | ✅ |  |
| `review_assessment` | approvals | ✅ | ✅ |  |  |  | ✅ |  |  |  |
| `approve_assessment` | approvals | ✅ |  |  |  |  | ✅ |  |  |  |
| `view_invoices` | finance | ✅ | ✅ | ✅ |  |  |  |  |  |  |
| `issue_invoice` | finance | ✅ |  | ✅ |  |  |  |  |  |  |
| `record_payment` | finance | ✅ |  | ✅ |  |  |  |  |  |  |
| `log_expense` | finance | ✅ |  | ✅ |  |  |  |  |  |  |
| `approve_requisition` | finance | ✅ | ✅ | ✅ |  |  |  |  |  |  |
| `view_financial_reports` | finance | ✅ | ✅ | ✅ |  |  |  |  |  |  |
| `view_reports` | reports | ✅ | ✅ | ✅ |  |  |  |  |  |  |
| `export_reports` | reports | ✅ | ✅ | ✅ |  |  |  |  |  |  |
| `view_employees` | hr | ✅ | ✅ |  | ✅ |  |  |  |  |  |
| `manage_employees` | hr | ✅ |  |  | ✅ |  |  |  |  |  |
| `view_attendance_records` | hr | ✅ | ✅ |  | ✅ |  | 👥 |  |  |  |
| `correct_attendance` | hr | ✅ |  |  | ✅ |  | 👥 |  |  |  |
| `view_attendance_reports` | hr | ✅ | ✅ |  | ✅ |  |  |  |  |  |
| `clock_in_out` | hr | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## What each permission allows

### settings

- `manage_users` — Create, edit and deactivate user accounts
- `manage_roles` — Create roles and grant permissions to them
- `manage_service_types` — Manage service types and their assessment question sets
- `manage_status_lists` — Manage ticket/project status option lists
- `manage_company_details` — Edit company details shown across the system
- `view_audit_log` — View the system audit log
### dashboard

- `view_dashboard` — View the role-aware dashboard landing screen
- `view_executive_dashboard` — View the executive command view
### crm

- `view_customers` — View the customer register
- `create_customer` — Create a new customer record
- `create_ticket` — Create a new ticket
- `assign_ticket` — Assign a ticket to a technician
- `view_ticket_status` — View ticket status and history
- `close_ticket` — Close a ticket
- `convert_ticket_to_project` — Convert a ticket into a project
### projects

- `view_projects` — View project records
- `manage_project` — Manage a project's tasks, crew and lifecycle stage
- `view_project_cost` — View a project's running cost and revenue
- `raise_requisition` — Raise a requisition against a project
### fieldjobs

- `view_own_job_list` — View own assigned field job list
- `gps_check_in` — GPS check in on arrival at a field job
- `submit_assessment` — Submit a structured site assessment
### approvals

- `review_assessment` — Review a submitted assessment
- `approve_assessment` — Approve or return a submitted assessment
### finance

- `view_invoices` — View invoices and payment status
- `issue_invoice` — Create and send an invoice
- `record_payment` — Record a payment against an invoice
- `log_expense` — Log an expense against a project
- `approve_requisition` — Approve a requisition (subject to the configured threshold)
- `view_financial_reports` — View financial reports
### reports

- `view_reports` — View the fixed operational report set
- `export_reports` — Export report data
### hr

- `view_employees` — View the employee register
- `manage_employees` — Create, edit and deactivate employee records
- `view_attendance_records` — View attendance records
- `correct_attendance` — Correct an attendance event, with a mandatory reason
- `view_attendance_reports` — View the monthly attendance report
- `clock_in_out` — Clock in and out, and view own attendance history
