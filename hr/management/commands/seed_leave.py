"""
Write the leave policy A-1 starts from into the database.

Idempotent, so it is safe on every deploy: nothing here overwrites a figure
HR has since changed, because the authoritative copy of policy is the
database and not this file. The one thing it will not do is change an
existing entitlement band — if A-1 has raised one, re-running this must not
quietly put it back.

    python manage.py seed_leave

The figures are the Decent Work Act of 2015 floor, converted to working days
on A-1's six-day week. They are a starting point an administrator can raise
in HR -> Leave settings, never a ceiling. The Act should be confirmed
against the statute itself before these are relied on for a dispute; the
sources behind them are listed in docs/leave-management-plan.md.
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from config.models import PolicySetting
from hr.models import LeaveEntitlementRule, LeaveType, PublicHoliday

# A-1 works Monday to Saturday, so a statutory week of leave is six working
# days. Changing the working week in Settings does not restate these: the
# bands are days, already converted, which is what keeps every screen from
# having to know the working week to show an allowance.
WORKING_DAYS_A_WEEK = 6

LEAVE_TYPES = [
    {
        "code": "annual",
        "label": "Annual leave",
        "description": "Paid time off, earned by length of service.",
        "is_paid": True,
        "document_required_after_days": None,
        "order": 10,
        "is_active": True,
        # Decent Work Act: one week in the first twelve months, two after
        # twelve, three after thirty-six, four after sixty.
        "bands": [(0, 1), (12, 2), (36, 3), (60, 4)],
        "bands_are_weeks": True,
    },
    {
        "code": "sick",
        "label": "Sick leave",
        "description": "Paid sick days. A medical note is asked for beyond three a year.",
        "is_paid": True,
        # The Act allows three undocumented sick days in a rolling twelve
        # months and requires documentation beyond that.
        "document_required_after_days": 3,
        "order": 20,
        "is_active": True,
        "bands": [(0, 10)],
        "bands_are_weeks": False,
    },
    {
        "code": "maternity",
        "label": "Maternity leave",
        "description": "Fourteen weeks paid, at least six of them after the birth.",
        "is_paid": True,
        "document_required_after_days": 0,
        "order": 30,
        "is_active": True,
        "bands": [(0, 14)],
        "bands_are_weeks": True,
    },
    {
        "code": "paternity",
        "label": "Paternity leave",
        "description": (
            "Switched off until A-1 sets the days. Paternity leave appears in "
            "Liberian press coverage but its statutory standing was not "
            "confirmed, so no figure is assumed here."
        ),
        "is_paid": True,
        "document_required_after_days": None,
        "order": 40,
        # Deliberately off. An allowance nobody has decided is worse than an
        # absent one: staff would ask against a number the company never
        # agreed to.
        "is_active": False,
        "bands": [],
        "bands_are_weeks": False,
    },
    {
        "code": "compassionate",
        "label": "Compassionate leave",
        "description": (
            "Bereavement and family emergencies. No annual allowance — each "
            "request is decided on its own facts."
        ),
        "is_paid": True,
        "document_required_after_days": None,
        "order": 50,
        "is_active": True,
        # No bands on purpose: this is untracked rather than zero, so HR
        # approves what the circumstances warrant instead of a balance
        # refusing somebody at a funeral.
        "bands": [],
        "bands_are_weeks": False,
    },
    {
        "code": "unpaid",
        "label": "Unpaid leave",
        "description": "Time off without pay, approved case by case.",
        "is_paid": False,
        "document_required_after_days": None,
        "order": 60,
        "is_active": True,
        "bands": [],
        "bands_are_weeks": False,
    },
]

# Liberia's eleven, under the Patriotic Observances Law. Fixed dates are
# given as (month, day); the movable ones as (month, weekday, nth) so the
# seed gets them right for whichever year it is run for. Weekday numbers are
# Python's: Monday is 0.
FIXED_HOLIDAYS = [
    (1, 1, "New Year's Day"),
    (2, 11, "Armed Forces Day"),
    (3, 15, "J. J. Roberts' Birthday"),
    (5, 14, "National Unification Day"),
    (7, 26, "Independence Day"),
    (8, 24, "Flag Day"),
    (11, 29, "President Tubman's Birthday"),
    (12, 25, "Christmas Day"),
]

MOVABLE_HOLIDAYS = [
    (3, 2, 2, "Decoration Day"),          # second Wednesday of March
    (4, 4, 2, "National Fast and Prayer Day"),  # second Friday of April
    (11, 3, 1, "Thanksgiving Day"),       # first Thursday of November
]


def nth_weekday(year, month, weekday, nth):
    """The nth given weekday of a month — the second Wednesday, and so on."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (nth - 1))


def holidays_for(year):
    days = [(date(year, month, day), name) for month, day, name in FIXED_HOLIDAYS]
    days += [
        (nth_weekday(year, month, weekday, nth), name)
        for month, weekday, nth, name in MOVABLE_HOLIDAYS
    ]
    return sorted(days)


class Command(BaseCommand):
    help = "Seed the leave types, entitlement bands and public holidays."

    def add_arguments(self, parser):
        parser.add_argument(
            "--years",
            type=int,
            default=2,
            help="How many years of public holidays to write, starting this year.",
        )
        parser.add_argument(
            "--tracked-from",
            default="",
            help="The date leave starts being counted here (default: today, "
                 "and only set if it has never been set).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        quiet = options.get("verbosity", 1) == 0
        today = timezone.localdate()

        def say(message):
            if not quiet:
                self.stdout.write(message)

        # ---- the working week and the go-live date -----------------------
        rest, created = PolicySetting.objects.get_or_create(
            key=PolicySetting.LEAVE_REST_DAYS,
            defaults={
                "value": "6",
                "description": "Rest days as weekday numbers, Monday 0. A-1 "
                               "works Monday to Saturday, so Sunday only.",
            },
        )
        say(
            self.style.SUCCESS(f"Rest days: {rest.value}")
            if created
            else f"  rest days already set to '{rest.value}' — left alone."
        )

        tracked_value = options["tracked_from"] or today.isoformat()
        tracked, created = PolicySetting.objects.get_or_create(
            key=PolicySetting.LEAVE_TRACKED_FROM,
            defaults={
                "value": tracked_value,
                "description": "Leave taken before this date is outside the "
                               "system; the balance screens say so.",
            },
        )
        say(
            self.style.SUCCESS(f"Leave tracked from: {tracked.value}")
            if created
            else f"  leave already tracked from {tracked.value} — left alone."
        )

        # ---- types and their bands --------------------------------------
        for spec in LEAVE_TYPES:
            bands = spec.pop("bands")
            as_weeks = spec.pop("bands_are_weeks")
            code = spec.pop("code")

            leave_type, created = LeaveType.objects.get_or_create(
                code=code, defaults=spec
            )
            if created:
                say(self.style.SUCCESS(f"Leave type '{leave_type.label}' created."))
            else:
                say(f"  leave type '{leave_type.label}' exists — left as it is.")

            for after_months, amount in bands:
                days = amount * WORKING_DAYS_A_WEEK if as_weeks else amount
                _rule, made = LeaveEntitlementRule.objects.get_or_create(
                    leave_type=leave_type,
                    after_months=after_months,
                    defaults={"days": days},
                )
                if made:
                    say(f"    {days} days after {after_months} months")

        # ---- public holidays --------------------------------------------
        written = 0
        for year in range(today.year, today.year + max(options["years"], 1)):
            for day, name in holidays_for(year):
                _holiday, made = PublicHoliday.objects.get_or_create(
                    date=day, defaults={"name": name}
                )
                written += int(made)
        say(self.style.SUCCESS(f"Public holidays: {written} written."))

        say(
            "\nThese are the statutory floor, converted to A-1's six-day "
            "week. Raise any of them in HR -> Leave settings; re-running this "
            "command will not put them back."
        )
        if not PublicHoliday.objects.filter(date__year=today.year + 1).exists():
            say(
                self.style.WARNING(
                    "No holidays for next year yet. HR should confirm the list "
                    "each December, because several of them move."
                )
            )
