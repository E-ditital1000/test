"""
The content rules in `a1.py`, asserted.

These filters are the one place the system decides how a date, a name or an
amount is written, so what they produce is a contract three modules rely on
rather than an implementation detail.

Dates are built relative to the current year on purpose: `a1date` prints the
year only when it is not this one, and a test written with a hardcoded year
would start failing on 1 January.
"""
from datetime import date

from django.test import SimpleTestCase
from django.utils import timezone

from .templatetags.a1 import a1daterange


class DateRangeTests(SimpleTestCase):
    """A task runs over a stretch of days. This is how one is written."""

    def setUp(self):
        self.year = timezone.localdate().year

    def test_within_one_month_the_month_is_said_once(self):
        self.assertEqual(
            a1daterange(date(self.year, 9, 3), date(self.year, 9, 12)), "3–12 Sept"
        )

    def test_across_two_months_both_are_named(self):
        self.assertEqual(
            a1daterange(date(self.year, 9, 28), date(self.year, 10, 3)), "28 Sept – 3 Oct"
        )

    def test_a_single_day_is_not_written_as_a_range(self):
        self.assertEqual(a1daterange(date(self.year, 9, 3), date(self.year, 9, 3)), "3 Sept")

    def test_one_end_alone_says_which_end_it_is(self):
        self.assertEqual(a1daterange(date(self.year, 9, 3), None), "from 3 Sept")
        self.assertEqual(a1daterange(None, date(self.year, 9, 12)), "due 12 Sept")

    def test_no_dates_at_all_produce_nothing(self):
        """So a screen can fall back to its own wording for undated work."""
        self.assertEqual(a1daterange(None, None), "")

    def test_a_range_in_another_year_carries_the_year(self):
        past = self.year - 1
        self.assertEqual(
            a1daterange(date(past, 9, 3), date(past, 9, 12)), f"3–12 Sept {past}"
        )
