"""
The design specification's content rules, as filters.

These exist so that ten agents building eight modules in parallel do not
each invent their own date, name and currency formatting. Section 10 of the
spec is a contract; this module is where it is enforced.

    {{ ticket.created_at|a1datetime }}   3 Sept 11:42
    {{ project.start_date|a1date }}      3 Sept   (3 Sept 2025 across years)
    {{ user|a1name }}                    M. Toe
    {{ employee|a1register_name }}       Toe, Moses
    {{ invoice.total|a1money }}          $18,400
"""
from decimal import Decimal, InvalidOperation

from django import template
from django.utils import timezone

register = template.Library()

# British abbreviations: three letters, except September. This is the form
# the specification uses throughout, and it is never numeric — "3/9" and
# "9/3" are both read in Liberia and the ambiguity is not worth four saved
# characters.
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"]


def _as_local(value):
    """Datetimes are stored in UTC; a person reads them in local time."""
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is not None:
        return timezone.localtime(value)
    return value


@register.filter
def a1date(value):
    """`3 Sept` inside the current year, `3 Sept 2025` across years."""
    if value is None:
        return ""
    value = _as_local(value)
    text = f"{value.day} {MONTHS[value.month - 1]}"
    if value.year != timezone.localdate().year:
        text += f" {value.year}"
    return text


@register.filter
def a1datetime(value):
    """`3 Sept 11:42` — the form every event timestamp takes."""
    if value is None:
        return ""
    value = _as_local(value)
    return f"{a1date(value)} {value:%H:%M}"


@register.filter
def a1name(value):
    """
    `M. Toe` — the operational form, used on tickets, jobs, activity logs
    and assignment, where the reader is scanning for a person they know.
    """
    if value is None:
        return ""
    first = (getattr(value, "first_name", "") or "").strip()
    last = (getattr(value, "last_name", "") or "").strip()
    if first and last:
        return f"{first[0]}. {last}"
    if last:
        return last
    if first:
        return first
    return getattr(value, "email", "") or str(value)


@register.filter
def a1register_name(value):
    """
    `Toe, Moses` — the register form, used in HR lists and reports only,
    where the list sorts by surname. The rule is the sort order, not the
    screen.
    """
    if value is None:
        return ""
    user = getattr(value, "user", value)
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    if first and last:
        return f"{last}, {first}"
    return last or first or getattr(user, "email", "") or str(user)


@register.filter
def a1money(value, precise=False):
    """
    `$18,400` in tiles, `$178.50` where precision matters. Phase One is
    single-currency, so the symbol is prefixed and the column header states
    the currency once rather than repeating it per row.
    """
    if value in (None, ""):
        return ""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ""
    negative = amount < 0
    amount = abs(amount)
    if precise or amount % 1:
        text = f"{amount:,.2f}"
    else:
        text = f"{amount:,.0f}"
    return f"{'-' if negative else ''}${text}"


@register.filter
def a1hours(minutes):
    """`6h 12m` — elapsed time, never a bare decimal on a field surface."""
    try:
        minutes = int(minutes or 0)
    except (TypeError, ValueError):
        return ""
    return f"{minutes // 60}h {minutes % 60:02d}m"


@register.simple_tag
def a1greeting():
    """
    "Good morning" / "Good afternoon" / "Good evening". Shared rather than
    reimplemented per module so the dashboard and the phone agree about what
    time of day it is.
    """
    hour = timezone.localtime(timezone.now()).hour
    if hour < 12:
        return "Good morning"
    return "Good afternoon" if hour < 17 else "Good evening"


@register.filter
def a1metres(value):
    """`± 9 m` — metric, with a space before the unit."""
    if value in (None, ""):
        return ""
    try:
        return f"± {round(float(value))} m"
    except (TypeError, ValueError):
        return ""


@register.filter
def dictkey(mapping, key):
    """
    Look a dictionary up by a key held in a variable. The report set renders
    generic rows against columns chosen at runtime, so it cannot use dotted
    attribute access.
    """
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def is_datetime(value):
    """True for a datetime, so a generic table can format one correctly."""
    import datetime

    return isinstance(value, datetime.datetime)
