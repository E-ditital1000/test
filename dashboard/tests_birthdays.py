"""
Birthdays on the dashboard. The part that matters is what is NOT shown: the
year of birth, anybody who has switched sharing off, anybody who has left.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile, Role, UserRole
from dashboard.birthdays import next_occurrence, occurrence, upcoming_birthdays

User = get_user_model()


def person(email, born=None, share=True, roles=(), active=True):
    first, last = email.split("@")[0].split(".")
    user = User.objects.create_user(
        username=email, email=email, password="Testing!12345",
        first_name=first.title(), last_name=last.title(), is_active=active,
    )
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in roles:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    Profile.objects.create(user=user, date_of_birth=born, show_birthday=share)
    return user


def born_in_1985(on):
    """A date of birth in 1985 whose birthday falls on `on`."""
    return occurrence(on, 1985) if on.month != 2 or on.day != 29 else date(1985, 2, 28)


class OccurrenceTests(SimpleTestCase):
    def test_a_leap_day_birthday_falls_on_the_28th_in_other_years(self):
        born = date(2000, 2, 29)
        self.assertEqual(occurrence(born, 2027), date(2027, 2, 28))
        self.assertEqual(occurrence(born, 2028), date(2028, 2, 29))

    def test_a_birthday_already_gone_this_year_is_next_year(self):
        self.assertEqual(next_occurrence(date(1990, 1, 5), date(2026, 9, 14)), date(2027, 1, 5))
        self.assertEqual(next_occurrence(date(1990, 9, 14), date(2026, 9, 14)), date(2026, 9, 14))


class UpcomingBirthdayTests(TestCase):
    def test_the_window_is_today_and_the_six_days_after(self):
        today = date(2026, 9, 14)
        person("in.today@t.local", date(1990, 9, 14))
        person("in.six@t.local", date(1990, 9, 20))
        person("out.seven@t.local", date(1990, 9, 21))
        person("out.past@t.local", date(1990, 9, 13))
        names = [b.user.email for b in upcoming_birthdays(today)]
        self.assertEqual(names, ["in.today@t.local", "in.six@t.local"])

    def test_the_week_runs_across_new_year(self):
        person("new.year@t.local", date(1990, 1, 2))
        found = upcoming_birthdays(date(2026, 12, 30))
        self.assertEqual([b.on for b in found], [date(2027, 1, 2)])

    def test_nobody_who_has_hidden_theirs_or_has_left_is_listed(self):
        from hr.models import Employee

        today = date(2026, 9, 14)
        person("hidden.one@t.local", date(1990, 9, 15), share=False)
        person("inactive.user@t.local", date(1990, 9, 15), active=False)
        left = person("left.company@t.local", date(1990, 9, 15))
        Employee.objects.create(user=left, staff_id="A1-LEFT", is_active=False)
        still = person("still.here@t.local", date(1990, 9, 15))
        Employee.objects.create(user=still, staff_id="A1-HERE", job_title="Electrician")
        person("no.record@t.local", date(1990, 9, 15))
        person("no.birthday@t.local", None)

        found = upcoming_birthdays(today)
        self.assertEqual(
            sorted(b.user.email for b in found),
            ["no.record@t.local", "still.here@t.local"],
        )
        self.assertEqual(
            next(b for b in found if b.user == still).job_title, "Electrician"
        )


class DashboardBirthdayTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)

    def test_colleagues_see_the_day_and_month_but_never_the_year(self):
        viewer = person("view.er@t.local", roles=["Admin"])
        soon = timezone.localdate() + timedelta(days=2)
        person("grace.mensah@t.local", born_in_1985(soon))

        self.client.force_login(viewer)
        body = self.client.get(reverse("dashboard-index")).content.decode()
        self.assertIn("Birthdays this week", body)
        self.assertIn("Grace Mensah", body)
        self.assertNotIn("1985", body)

    def test_the_card_is_absent_in_a_week_with_no_birthdays(self):
        viewer = person("view.er@t.local", roles=["Admin"])
        self.client.force_login(viewer)
        body = self.client.get(reverse("dashboard-index")).content.decode()
        self.assertNotIn("Birthdays this week", body)

    def test_the_person_whose_birthday_it_is_is_greeted_even_if_they_hide_it(self):
        today = timezone.localdate()
        me = person("birth.day@t.local", born_in_1985(today), share=False, roles=["Admin"])
        self.client.force_login(me)
        body = self.client.get(reverse("dashboard-index")).content.decode()
        if today.month == 2 and today.day == 29:
            self.skipTest("a 1985 birthday cannot fall on 29 February")
        self.assertIn("Happy birthday, Birth", body)
        # Hidden means hidden, including from their own colleague list.
        self.assertNotIn("Birthdays this week", body)

    def test_switching_it_off_on_the_profile_takes_them_off_the_card(self):
        viewer = person("view.er@t.local", roles=["Admin"])
        soon = timezone.localdate() + timedelta(days=1)
        grace = person("grace.mensah@t.local", born_in_1985(soon))

        self.client.force_login(grace)
        self.client.post(reverse("profile"), {"date_of_birth": born_in_1985(soon).isoformat()})
        self.assertFalse(Profile.objects.get(user=grace).show_birthday)

        self.client.force_login(viewer)
        body = self.client.get(reverse("dashboard-index")).content.decode()
        self.assertNotIn("Grace Mensah", body)
