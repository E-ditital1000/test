"""
The dashboard's charts.

A-1 asked for the dashboard to show the shape of the work rather than a
ranked list of it in red. These pin the figures behind the pictures, and the
few rules that are easy to break silently later: a bar that leaves its own
plot area, a palette somebody swaps for brand colours that fail, a series
identified by colour alone.
"""
from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, UserRole
from config.models import PolicySetting, ServiceType, StatusOption
from crm.models import Customer, Ticket
from dashboard import charts
from hr.models import AttendanceDay, Employee

User = get_user_model()


def make_user(email, role_names=()):
    user = User.objects.create_user(
        username=email, email=email, password="Testing!12345"
    )
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class AttendanceChartTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        PolicySetting.objects.create(key=PolicySetting.LEAVE_REST_DAYS, value="6")
        cls.admin = make_user("viz.admin@test.local", ["Admin"])
        cls.people = [
            Employee.objects.create(
                user=make_user(f"viz{n}@test.local"), staff_id=f"A1-V{n}",
                start_date=date(2024, 1, 8),
            )
            for n in range(4)
        ]

    def test_a_rest_day_is_not_drawn_at_all(self):
        """
        A Sunday column of zeroes is not information about attendance, it is
        a hole somebody has to learn to ignore.
        """
        week = charts.attendance_week(self.admin, days=7)
        self.assertTrue(week["days"])
        for day in week["days"]:
            self.assertNotEqual(day["date"].weekday(), 6, "Sunday must be skipped")

    def test_everybody_is_accounted_for_on_every_day(self):
        """
        The four bands are the whole headcount. A day that does not add up
        is a bar that lies about the size of the team.
        """
        week = charts.attendance_week(self.admin)
        for day in week["days"]:
            self.assertEqual(
                day["worked"] + day["late"] + day["leave"] + day["absent"],
                week["headcount"],
            )

    def test_somebody_who_worked_is_counted_as_worked(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        if yesterday.weekday() == 6:
            yesterday -= timedelta(days=1)
        AttendanceDay.objects.create(
            employee=self.people[0], date=yesterday,
            state=AttendanceDay.PRESENT, total_minutes=480,
        )
        week = charts.attendance_week(self.admin)
        day = next(d for d in week["days"] if d["date"] == yesterday)
        self.assertEqual(day["worked"], 1)
        self.assertEqual(day["absent"], len(self.people) - 1)

    @tag("acceptance")
    def test_no_segment_escapes_the_plot_area(self):
        week = {
            "headcount": 16, "max": 16,
            "days": [{"date": date(2026, 10, 12), "worked": 9, "late": 2,
                      "leave": 1, "absent": 4}],
        }
        for column in charts.attendance_columns(week):
            for segment in column["segments"]:
                self.assertGreaterEqual(segment["y"], 0)
                self.assertLessEqual(
                    segment["y"] + segment["height"], charts.BAR_HEIGHT,
                    "a bar outside its own plot area is a drawing bug",
                )
                self.assertGreater(segment["height"], 0)

    def test_one_person_in_sixteen_is_still_visible(self):
        """Rounded to nothing, a real absence would simply disappear."""
        week = {
            "headcount": 16, "max": 16,
            "days": [{"date": date(2026, 10, 12), "worked": 15, "late": 0,
                      "leave": 0, "absent": 1}],
        }
        column = charts.attendance_columns(week)[0]
        absent = next(s for s in column["segments"] if s["key"] == "absent")
        self.assertGreaterEqual(absent["height"], 1)

    def test_a_band_with_nobody_in_it_is_not_drawn(self):
        week = {
            "headcount": 4, "max": 4,
            "days": [{"date": date(2026, 10, 12), "worked": 4, "late": 0,
                      "leave": 0, "absent": 0}],
        }
        column = charts.attendance_columns(week)[0]
        self.assertEqual([s["key"] for s in column["segments"]], ["worked"])

    def test_an_empty_register_draws_nothing_rather_than_an_empty_axis(self):
        Employee.objects.all().delete()
        week = charts.attendance_week(self.admin)
        self.assertEqual(week["days"], [])
        self.assertEqual(week["headcount"], 0)


class TicketChartTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0, reset_system_roles=True)
        cls.admin = make_user("viz.t@test.local", ["Admin"])
        cls.service = ServiceType.objects.create(code="solar", name="Solar")
        cls.status = StatusOption.objects.create(
            kind=StatusOption.TICKET, code="new", label="New", is_default=True
        )
        cls.customer = Customer.objects.create(name="Duport Road Clinic")

    def _ticket(self, reference, closed=False):
        ticket = Ticket.objects.create(
            reference=reference, customer=self.customer, service_type=self.service,
            status=self.status, description="Inverter fault.",
        )
        if closed:
            ticket.closed_at = timezone.now()
            ticket.save(update_fields=["closed_at"])
        return ticket

    def test_nothing_raised_means_nothing_to_plot(self):
        fortnight = charts.tickets_fortnight()
        self.assertFalse(fortnight["has_data"])

    def test_raised_and_closed_are_counted_separately(self):
        """
        Two series, not one net line: "three in, five out" and "nothing
        happened" both net to minus two and mean different things.
        """
        self._ticket("TKT-1")
        self._ticket("TKT-2", closed=True)
        fortnight = charts.tickets_fortnight()
        self.assertEqual(fortnight["total_raised"], 2)
        self.assertEqual(fortnight["total_closed"], 1)
        self.assertEqual(fortnight["direction"], "grew")
        self.assertEqual(fortnight["net"], 1)

    def test_a_balanced_fortnight_says_so(self):
        self._ticket("TKT-3", closed=True)
        fortnight = charts.tickets_fortnight()
        self.assertEqual(fortnight["direction"], "level")

    def test_both_series_share_one_scale(self):
        """Never two y-axes. One ceiling, both lines measured against it."""
        self._ticket("TKT-4")
        lines = charts.ticket_lines(charts.tickets_fortnight())
        for marker in lines["markers"]:
            for key in ("raised_y", "closed_y"):
                self.assertGreaterEqual(marker[key], 0)
                self.assertLessEqual(marker[key], charts.LINE_HEIGHT)

    @tag("acceptance")
    def test_the_chart_draws_once_there_is_data(self):
        self._ticket("TKT-5")
        self.client.force_login(self.admin)
        body = self.client.get(reverse("dashboard-index")).content.decode()
        self.assertIn("Tickets in and out", body)
        self.assertIn("polyline", body)
        self.assertIn("Raised", body)
        self.assertIn("Closed", body)
        self.assertNotIn("nothing to plot yet", body)


class ChartPaletteTests(TestCase):
    """
    The palette is computable, so it is checked rather than eyeballed.

    A-1's own status colours fail these: navy #372482 sits outside the
    lightness band and teal #0A6068 reads as grey at fill size, because both
    are tuned for text. If somebody later swaps the chart slots for brand
    colours to make it "look right", this is what says no.
    """

    from pathlib import Path

    CSS = (Path(__file__).resolve().parent.parent / "static" / "css" / "a1.css")

    def _slots(self):
        import re

        css = self.CSS.read_text(encoding="utf-8")
        return {
            name: value
            for name, value in re.findall(r"--(viz-[\w-]+):\s*(#[0-9a-fA-F]{6})", css)
        }

    def test_the_validated_categorical_slots_are_what_ships(self):
        slots = self._slots()
        self.assertEqual(slots.get("viz-1"), "#2a78d6")
        self.assertEqual(slots.get("viz-2"), "#eb6834")

    def test_the_brand_status_colours_are_not_used_as_series(self):
        """They fail the chart checks; they are text colours."""
        slots = self._slots()
        for failing in ("#372482", "#0a6068", "#0A6068", "#C4321E"):
            self.assertNotIn(failing, (slots.get("viz-1"), slots.get("viz-2")))

    def test_gold_is_never_a_series(self):
        """The design system reserves it for the action and nothing else."""
        slots = self._slots()
        self.assertNotIn("#FFCB00", slots.values())
        self.assertNotIn("#ffcb00", slots.values())

    def test_on_leave_is_not_dressed_as_a_failure(self):
        """
        It is accounted for, not an exception — so it is deliberately a
        neutral rather than one of the reserved status colours.
        """
        slots = self._slots()
        self.assertEqual(slots.get("viz-neutral"), "#9aa1b4")
        self.assertNotEqual(slots.get("viz-neutral"), slots.get("viz-critical"))
