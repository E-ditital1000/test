"""
Colleagues' birthdays coming up this week, for the dashboard.

What is shared is deliberately thin: a name, a day and a month. Never the
year, so nobody's age can be read off the screen, and never anybody who has
switched sharing off on their profile or who no longer works here.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

WINDOW_DAYS = 7


@dataclass
class Birthday:
    user: object
    photo_url: str
    job_title: str
    on: date          # this year's occurrence, not the date of birth
    days_away: int

    @property
    def is_today(self):
        return self.days_away == 0

    @property
    def is_tomorrow(self):
        return self.days_away == 1


def occurrence(born, year):
    """
    The day a birthday falls on in `year`. Somebody born on 29 February
    celebrates on the 28th in a year that has no 29th, rather than never.
    """
    try:
        return born.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def next_occurrence(born, today):
    this_year = occurrence(born, today.year)
    return this_year if this_year >= today else occurrence(born, today.year + 1)


def upcoming_birthdays(today=None, days=WINDOW_DAYS):
    """Everyone sharing a birthday in the next `days` days, soonest first."""
    from accounts.models import Profile

    today = today or timezone.localdate()
    profiles = (
        Profile.objects.filter(
            show_birthday=True,
            date_of_birth__isnull=False,
            user__is_active=True,
        )
        # Somebody with no employee record (an admin account) still counts;
        # somebody whose employee record was deactivated has left.
        .filter(Q(user__employee__isnull=True) | Q(user__employee__is_active=True))
        .select_related("user", "user__employee")
    )

    found = []
    for profile in profiles:
        on = next_occurrence(profile.date_of_birth, today)
        days_away = (on - today).days
        if days_away >= days:
            continue
        employee = getattr(profile.user, "employee", None)
        found.append(
            Birthday(
                user=profile.user,
                photo_url=profile.photo.url if profile.photo else "",
                job_title=getattr(employee, "job_title", "") or "",
                on=on,
                days_away=days_away,
            )
        )
    found.sort(key=lambda b: (b.days_away, b.user.first_name, b.user.last_name))
    return found


def is_birthday_today(user, today=None):
    """For the person themselves, whether or not they share it."""
    from accounts.models import Profile

    today = today or timezone.localdate()
    born = Profile.objects.filter(user=user).values_list("date_of_birth", flat=True).first()
    return bool(born) and occurrence(born, today.year) == today
