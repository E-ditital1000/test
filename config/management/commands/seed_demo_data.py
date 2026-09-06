"""
The seeded demo dataset from the Wave 1 brief: the day-one configuration
(service types with question sets, status lists, correction reasons, policy
values) plus 12 employees including 3 supervisors, 6 customers, 10 tickets
and 5 projects.

Idempotent — every row is looked up by a natural key first, so running it
twice does not double the dataset. It seeds demo *data*; it never grants
anybody permissions outside the pre-built roles.
"""
import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, UserRole
from config.models import (
    AssessmentQuestion,
    CompanyDetail,
    CorrectionReason,
    PolicySetting,
    ServiceType,
    StatusOption,
)
from crm.models import Contact, Customer, Site, Ticket
from finance.models import ExpenseCategory
from hr.models import Employee
from projects.models import Project

User = get_user_model()

SERVICE_TYPES = [
    ("electrical_install", "Electrical Installation"),
    ("solar_install", "Solar Installation"),
    ("maintenance", "Maintenance & Service Call"),
]

QUESTION_SETS = {
    "electrical_install": [
        ("Is the distribution board accessible?", "boolean", "technician", True),
        ("Existing cable size (mm2)", "number", "technician", True),
        ("Condition of existing earthing", "text", "technician", True),
        ("Photographs of the board taken?", "boolean", "technician", True),
        ("Any additional work requested on site?", "long_text", "client", False),
        ("Client confirms access arrangements", "boolean", "client", True),
    ],
    "solar_install": [
        ("Roof type", "choice", "technician", True),
        ("Usable roof area (m2)", "number", "technician", True),
        ("Shading observed during the visit", "long_text", "technician", True),
        ("Inverter location agreed", "text", "technician", True),
        ("Average monthly electricity spend", "number", "client", False),
        ("Client's priority: backup or savings?", "choice", "client", True),
    ],
    "maintenance": [
        ("Fault reported by the client", "long_text", "client", True),
        ("Fault found on inspection", "long_text", "technician", True),
        ("Parts required", "text", "technician", False),
        ("Is the installation safe to leave energised?", "boolean", "technician", True),
    ],
}

CHOICES = {
    "Roof type": ["Corrugated iron", "Concrete tile", "Flat concrete", "Thatch"],
    "Client's priority: backup or savings?": ["Backup", "Savings", "Both"],
}

TICKET_STATUSES = [
    ("new", "New", True, False),
    ("assigned", "Assigned", False, False),
    ("in_progress", "In progress", False, False),
    ("closed", "Closed", False, True),
]

PROJECT_STATUSES = [
    ("active", "Active", True, False),
    ("on_hold", "On hold", False, False),
    ("completed", "Completed", False, True),
]

CORRECTION_REASONS = [
    ("forgot_to_clock", "Forgot to clock in/out"),
    ("phone_offline", "Phone offline or out of battery"),
    ("wrong_time", "Clocked at the wrong time"),
    ("approved_offsite", "Approved off-site work"),
    ("system_error", "System or sync error"),
]

EXPENSE_CATEGORIES = [
    ("materials", "Materials"),
    ("transport", "Transport & fuel"),
    ("subcontractor", "Subcontractor"),
    ("equipment_hire", "Equipment hire"),
    ("consumables", "Consumables"),
]

PEOPLE = [
    ("Ada", "Okafor", "supervisor"),
    ("Bello", "Musa", "supervisor"),
    ("Chidinma", "Eze", "supervisor"),
    ("Daniel", "Adeyemi", "technician"),
    ("Esther", "Nwosu", "technician"),
    ("Femi", "Balogun", "technician"),
    ("Grace", "Uche", "technician"),
    ("Hassan", "Ibrahim", "technician"),
    ("Ifeoma", "Obi", "technician"),
    ("Jide", "Coker", "technician"),
    ("Kemi", "Alabi", "technician"),
    ("Lanre", "Ojo", "technician"),
]

# One signed-in account per pre-built role, so every role can actually be
# tried. These are office and system users, not field staff: they get no
# Employee record, which keeps the register at the 12 the brief specifies and
# is why they see the clock screen's "not on the register" state.
# Named after the cast in the approved mockups, so screenshots and seed data
# tell the same story.
ROLE_ACCOUNTS = [
    ("Amara", "Bedell", "Executive"),
    ("Gayflor", "Sirleaf", "Finance"),
    ("Miatta", "Johnson", "HR"),
    ("Roseline", "Quaye", "Receptionist"),
    ("Prince", "Wesseh", "Project Manager"),
    ("Comfort", "Kollie", "Admin"),
    # The floor every staff account starts on: clock in/out and their own
    # attendance, nothing else.
    ("Rebecca", "Doe", "Employee"),
]

CUSTOMERS = [
    ("Harmony Foods Ltd", "Ikeja"),
    ("Riverbend Estate", "Lekki"),
    ("St. Mark's School", "Yaba"),
    ("Copperfield Hotels", "Victoria Island"),
    ("Novatech Offices", "Surulere"),
    ("Green Valley Farms", "Epe"),
]


class Command(BaseCommand):
    help = "Seed the day-one configuration and a demo dataset for Wave 1 demos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="Demo!Pass123",
            help="Password given to every seeded demo account.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(1360)  # A repeatable dataset: the same demo every time.
        self.password = options["password"]

        self._company()
        service_types = self._service_types()
        self._statuses()
        self._correction_reasons()
        self._expense_categories()
        self._policy()
        employees = self._people()
        self._role_accounts()
        customers = self._customers()
        tickets = self._tickets(customers, service_types)
        self._projects(tickets, employees)

        self.stdout.write(self.style.SUCCESS("Demo dataset seeded."))
        self.stdout.write(
            "  Sign in with any seeded email (e.g. ada.okafor@a1edigital.test) "
            "and the seeded password; the forced reset will prompt for a new one."
        )

    # -- configuration ----------------------------------------------------

    def _company(self):
        company = CompanyDetail.get()
        if not company.phone:
            company.name = "A-1 E-Digital Network"
            company.address = "Lagos, Nigeria"
            company.phone = "+234 800 000 0000"
            company.email = "info@a1edigital.test"
            company.save()

    def _service_types(self):
        created = {}
        for order, (code, name) in enumerate(SERVICE_TYPES, start=1):
            service_type, _ = ServiceType.objects.get_or_create(
                code=code, defaults={"name": name, "order": order}
            )
            created[code] = service_type
            for position, (text, answer_type, respondent, required) in enumerate(
                QUESTION_SETS[code], start=1
            ):
                AssessmentQuestion.objects.get_or_create(
                    service_type=service_type,
                    text=text,
                    defaults={
                        "answer_type": answer_type,
                        "respondent": respondent,
                        "is_required": required,
                        "order": position,
                        "choices": CHOICES.get(text, []),
                    },
                )
        self.stdout.write(f"  service types: {len(created)}")
        return created

    def _statuses(self):
        for kind, rows in ((StatusOption.TICKET, TICKET_STATUSES), (StatusOption.PROJECT, PROJECT_STATUSES)):
            for order, (code, label, is_default, is_terminal) in enumerate(rows, start=1):
                StatusOption.objects.get_or_create(
                    kind=kind,
                    code=code,
                    defaults={
                        "label": label,
                        "order": order,
                        "is_default": is_default,
                        "is_terminal": is_terminal,
                    },
                )

    def _correction_reasons(self):
        for order, (code, label) in enumerate(CORRECTION_REASONS, start=1):
            CorrectionReason.objects.get_or_create(
                code=code, defaults={"label": label, "order": order}
            )

    def _expense_categories(self):
        for order, (code, name) in enumerate(EXPENSE_CATEGORIES, start=1):
            ExpenseCategory.objects.get_or_create(code=code, defaults={"name": name, "order": order})

    def _policy(self):
        defaults = [
            (
                PolicySetting.REQUISITION_THRESHOLD,
                "100000",
                "Requisitions above this amount need executive approval.",
            ),
            (
                PolicySetting.WORKDAY_START,
                "08:00",
                "Standard start of the working day.",
            ),
            (
                PolicySetting.LATE_AFTER_MINUTES,
                "15",
                "Grace period in minutes before a clock-in counts as late.",
            ),
        ]
        for key, value, description in defaults:
            PolicySetting.objects.get_or_create(
                key=key, defaults={"value": value, "description": description}
            )

    # -- people -----------------------------------------------------------

    def _people(self):
        roles = {role.name: role for role in Role.objects.all()}
        if not roles:
            self.stdout.write(
                self.style.WARNING("  no roles found — run `seed_permissions` first.")
            )

        employees = []
        supervisors = []
        for index, (first, last, kind) in enumerate(PEOPLE, start=1):
            email = f"{first.lower()}.{last.lower()}@a1edigital.test"
            user, created = User.objects.get_or_create(
                username=email,
                defaults={"first_name": first, "last_name": last, "email": email},
            )
            if created:
                user.set_password(self.password)
                user.must_reset_password = True
                user.save()

            role_name = "Supervisor" if kind == "supervisor" else "Technician"
            if role_name in roles:
                UserRole.objects.get_or_create(user=user, role=roles[role_name])

            employee, _ = Employee.objects.get_or_create(
                user=user,
                defaults={
                    "staff_id": f"A1-{index:03d}",
                    "job_title": role_name,
                    "department": "Operations",
                    "phone": f"+23480{index:08d}",
                    "start_date": timezone.localdate() - timedelta(days=200 + index * 7),
                },
            )
            employees.append(employee)
            if kind == "supervisor":
                supervisors.append(employee)

        # Spread the technicians across the three supervisors, so "own team"
        # scope has something real to filter on in a demo.
        technicians = [e for e in employees if e not in supervisors]
        for position, employee in enumerate(technicians):
            if employee.supervisor_id is None:
                employee.supervisor = supervisors[position % len(supervisors)]
                employee.save(update_fields=["supervisor"])

        self.stdout.write(f"  employees: {len(employees)} ({len(supervisors)} supervisors)")
        return employees

    def _role_accounts(self):
        """One account per pre-built role that has nobody assigned to it."""
        roles = {role.name: role for role in Role.objects.all()}
        created = 0
        for first, last, role_name in ROLE_ACCOUNTS:
            role = roles.get(role_name)
            if role is None:
                continue
            email = f"{first.lower()}.{last.lower()}@a1edigital.test"
            user, was_new = User.objects.get_or_create(
                username=email,
                defaults={"first_name": first, "last_name": last, "email": email},
            )
            if was_new:
                user.set_password(self.password)
                # These exist to be signed into while building, so they skip
                # the forced reset the real staff accounts get.
                user.must_reset_password = False
                user.save()
                created += 1
            UserRole.objects.get_or_create(user=user, role=role)
        self.stdout.write(f"  role accounts: {created} created")

    # -- customers, tickets, projects -------------------------------------

    def _customers(self):
        customers = []
        for name, area in CUSTOMERS:
            customer, _ = Customer.objects.get_or_create(
                name=name,
                defaults={
                    "phone": "+234 700 000 0000",
                    "email": f"contact@{name.split()[0].lower()}.test",
                    "address": f"{area}, Lagos",
                },
            )
            Site.objects.get_or_create(
                customer=customer,
                name=f"{area} site",
                defaults={"address": f"{area}, Lagos"},
            )
            Contact.objects.get_or_create(
                customer=customer,
                name="Facilities Manager",
                defaults={"phone": "+234 700 111 2222", "is_primary": True},
            )
            customers.append(customer)
        self.stdout.write(f"  customers: {len(customers)}")
        return customers

    def _tickets(self, customers, service_types):
        status_new = StatusOption.objects.get(kind=StatusOption.TICKET, code="new")
        status_assigned = StatusOption.objects.get(kind=StatusOption.TICKET, code="assigned")
        technicians = list(User.objects.filter(employee__job_title="Technician"))
        service_type_list = list(service_types.values())

        tickets = []
        for number in range(1, 11):
            reference = f"TKT-{number:04d}"
            customer = customers[(number - 1) % len(customers)]
            assigned = technicians[number % len(technicians)] if technicians and number % 3 else None
            ticket, _ = Ticket.objects.get_or_create(
                reference=reference,
                defaults={
                    "customer": customer,
                    "site": customer.sites.first(),
                    "service_type": service_type_list[number % len(service_type_list)],
                    "status": status_assigned if assigned else status_new,
                    "priority": random.choice([t[0] for t in Ticket.PRIORITIES]),
                    "description": f"Reported issue #{number} at {customer.name}.",
                    "assigned_to": assigned,
                    "assigned_at": timezone.now() if assigned else None,
                },
            )
            tickets.append(ticket)
        self.stdout.write(f"  tickets: {len(tickets)}")
        return tickets

    def _projects(self, tickets, employees):
        status = StatusOption.objects.get(kind=StatusOption.PROJECT, code="active")
        managers = list(User.objects.filter(employee__job_title="Supervisor"))

        projects = []
        for number, ticket in enumerate(tickets[:5], start=1):
            reference = f"PRJ-{number:04d}"
            project, created = Project.objects.get_or_create(
                reference=reference,
                defaults={
                    "name": f"{ticket.customer.name} — {ticket.service_type.name}",
                    "description": ticket.description,
                    # Converting carries the ticket's lineage forward, which
                    # is what makes the job traceable end to end.
                    "job_ref": ticket.job_ref,
                    "ticket": ticket,
                    "customer": ticket.customer,
                    "site": ticket.site,
                    "service_type": ticket.service_type,
                    "status": status,
                    "stage": Project.EXECUTION if number % 2 else Project.SITE_ASSESSMENT,
                    "manager": managers[number % len(managers)] if managers else None,
                    "start_date": timezone.localdate() - timedelta(days=number * 5),
                },
            )
            projects.append(project)
        self.stdout.write(f"  projects: {len(projects)}")
        return projects
