"""
Send one of every message the system can send, to an address you choose.

For proving the thing end to end: that the mailbox works, that each message
reads properly on a phone, and that none of them carries anything it should
not.

    python manage.py mail_preview you@example.com

The samples are built from real objects, so a template reading a field
nobody set fails here rather than in front of a customer. Everything is
made inside a transaction that is rolled back, so a preview leaves no
ticket, account or clock event behind. Mail is the only thing that leaves.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class _Rollback(Exception):
    """Thrown to undo the samples; never escapes `handle`."""


class Command(BaseCommand):
    help = "Send a sample of every notification to one address."

    def add_arguments(self, parser):
        parser.add_argument("address", help="Where to send the samples.")
        parser.add_argument(
            "--only", default="", help="One template name, instead of all of them."
        )

    def handle(self, *args, **options):
        from django.conf import settings

        address = options["address"]
        only = options["only"]

        if settings.EMAIL_BACKEND.endswith("console.EmailBackend"):
            self.stdout.write(self.style.WARNING(
                "EMAIL_HOST is not set, so these are printed below rather than "
                "sent. Set it to send for real."
            ))

        sent = []
        try:
            with transaction.atomic():
                from config import notifications

                for name, subject, context in self._samples():
                    if only and name != only:
                        continue
                    ok = notifications.send(
                        to=address, subject=subject, template=name, context=context
                    )
                    sent.append((name, subject, ok))
                raise _Rollback()
        except _Rollback:
            pass

        if not sent:
            raise CommandError("No template called '{}'.".format(only))
        for name, subject, ok in sent:
            mark = self.style.SUCCESS("sent") if ok else self.style.ERROR("FAILED")
            self.stdout.write("  {}  {:22} {}".format(mark, name, subject))
        self.stdout.write("\n{} message(s) to {}.".format(len(sent), address))

    def _samples(self):
        from datetime import date, timedelta

        from django.contrib.auth import get_user_model
        from django.utils import timezone

        from accounts.templatetags.a1 import a1daterange, a1datetime, a1money
        from config import notifications
        from config.models import ServiceType, StatusOption
        from crm.models import Customer, Site, Ticket
        from fieldjobs.models import Assessment, FieldJob
        from hr.models import Department, Employee
        from projects.models import Project, Requisition, Task

        User = get_user_model()
        today = date.today()
        link = notifications.link

        service = ServiceType.objects.first() or ServiceType.objects.create(
            code="sample-solar", name="Solar Installation"
        )
        ticket_status = StatusOption.objects.filter(
            kind=StatusOption.TICKET
        ).first() or StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        project_status = StatusOption.objects.filter(
            kind=StatusOption.PROJECT
        ).first() or StatusOption.objects.create(
            kind=StatusOption.PROJECT, code="active", label="Active", is_default=True
        )

        customer = Customer.objects.create(name="Catholic Relief Services")
        site = Site.objects.create(
            customer=customer, name="Zwedru Health Centre", address="Zwedru, Grand Gedeh"
        )
        technician = User.objects.create_user(
            username="sample.tech@example.invalid",
            email="sample.tech@example.invalid",
            first_name="Moses", last_name="Toe",
        )
        supervisor = User.objects.create_user(
            username="sample.sup@example.invalid",
            email="sample.sup@example.invalid",
            first_name="Grace", last_name="Kollie",
        )
        department = Department.objects.first() or Department.objects.create(
            name="Field Operations"
        )
        employee = Employee.objects.create(
            user=technician, staff_id="A1-0042",
            job_title="Senior Technician", department=department,
        )

        ticket = Ticket.objects.create(
            reference="TKT-0042", customer=customer, site=site,
            service_type=service, status=ticket_status, priority="high",
            description=(
                "No power to the theatre since Tuesday morning. The inverter "
                "is showing a fault light and the batteries are not charging."
            ),
            start_date=today, due_date=today + timedelta(days=2),
        )
        project = Project.objects.create(
            reference="PRJ-0042",
            name="CRS: 50 kW solar at 12 health facilities",
            customer=customer, site=site, service_type=service,
            status=project_status, manager=supervisor, start_date=today,
            target_end_date=today + timedelta(days=182),
            description="Twelve facilities across Grand Gedeh and Nimba, six months.",
        )
        task = Task.objects.create(
            project=project, title="Torque the array bolts", assignee=technician,
            description="38 Nm, both rails. Bring the calibrated wrench.",
            start_date=today, due_date=today + timedelta(days=1),
        )
        visit = FieldJob.objects.create(
            reference="FJ-0042", project=project, customer=customer, site=site,
            service_type=service, assigned_to=technician,
            scheduled_for=timezone.now(), job_ref=project.job_ref,
        )
        assessment = Assessment.objects.create(
            field_job=visit, service_type=service, technician=technician,
            client_uuid="00000000-0000-0000-0000-000000000042",
            device_timestamp=timezone.now(), location_unavailable=True,
        )
        requisition = Requisition.objects.create(
            project=project, reference="RQ-0042",
            description="Four 450 W panels and 30 m of 6 mm cable for the theatre run.",
            amount=1850, raised_by=technician,
            needed_by=today + timedelta(days=4),
        )

        return [
            (
                "account_created",
                "An account has been created for you",
                {
                    "user": technician, "first_name": "Moses",
                    "roles": "Technician, Employee", "staff_id": "A1-0042",
                    "created_by": "Grace Kollie", "url": link("login"),
                },
            ),
            (
                "ticket_assigned",
                "{} assigned to you".format(ticket.reference),
                {
                    "ticket": ticket, "technician_name": "Moses",
                    "assigned_by": "Grace Kollie",
                    "dates": a1daterange(ticket.start_date, ticket.due_date),
                    "url": link("crm-ticket-detail", ticket.pk),
                },
            ),
            (
                "task_assigned",
                "Task on {} - {}".format(project.reference, task.title),
                {
                    "task": task, "project": project, "visit": visit,
                    "first_name": "Moses", "assigned_by": "Grace Kollie",
                    "dates": a1daterange(task.start_date, task.due_date),
                    "url": link("projects-detail", project.pk),
                },
            ),
            (
                "project_assigned",
                "{} is yours to run".format(project.reference),
                {
                    "project": project, "first_name": "Grace",
                    "started_by": "Daniel M C Padmore",
                    "dates": a1daterange(project.start_date, project.target_end_date),
                    "from_ticket": ticket.reference,
                    "url": link("projects-detail", project.pk),
                },
            ),
            (
                "clock_event",
                "Moses Toe clocked in",
                {
                    "employee": employee, "employee_name": "Moses Toe",
                    "direction": "in", "at": a1datetime(timezone.now()),
                    "location": "+/- 9 m", "code": "Ganta yard",
                    "url": link("hr-employee-attendance", employee.pk),
                },
            ),
            (
                "clock_event",
                "Moses Toe clocked out",
                {
                    "employee": employee, "employee_name": "Moses Toe",
                    "direction": "out", "at": a1datetime(timezone.now()),
                    "location": "not available", "code": "Ganta yard",
                    "url": link("hr-employee-attendance", employee.pk),
                },
            ),
            (
                "assessment_submitted",
                "Assessment to review - {}".format(visit.reference),
                {
                    "assessment": assessment,
                    "submitted": a1datetime(timezone.now()),
                    "submitted_by": "Moses Toe",
                    "answer_count": 7, "photo_count": 3,
                    "url": link("approvals-queue"),
                },
            ),
            (
                "requisition_raised",
                "Requisition to approve - {}".format(requisition.reference),
                {
                    "requisition": requisition,
                    "amount": a1money(requisition.amount),
                    "raised_by": "Moses Toe",
                    "needed": requisition.needed_by.strftime("%d %b")
                    if hasattr(requisition.needed_by, "strftime") else "",
                    "threshold_note": "Finance can approve it.",
                    "url": link("finance-requisitions"),
                },
            ),
        ]
