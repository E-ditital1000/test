"""
The design specification's content rules, as filters.

These exist so that ten agents building eight modules in parallel do not
each invent their own date, name and currency formatting. Section 10 of the
spec is a contract; this module is where it is enforced.

    {{ ticket.created_at|a1datetime }}   3 Sept 11:42
    {{ project.start_date|a1date }}      3 Sept   (3 Sept 2025 across years)
    {{ t.start_date|a1daterange:t.due_date }}  3–12 Sept
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
def a1daterange(start, end):
    """
    A stretch of days, in the least ink that stays exact:

        {{ task.start_date|a1daterange:task.due_date }}

        3–12 Sept        within one month
        28 Sept – 3 Oct  across two
        3 Sept           a single day
        from 3 Sept      no end given
        due 12 Sept      no start given

    One filter rather than a date at each end, so the project page, the
    dashboard and the phone cannot drift into writing a range three ways.
    """
    if start is None and end is None:
        return ""
    if start is None:
        return f"due {a1date(end)}"
    if end is None:
        return f"from {a1date(start)}"
    if start == end:
        return a1date(start)
    # Within one month the month is said once. The year, where a1date adds
    # it, comes from the end date and so covers both.
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{a1date(end)}"
    return f"{a1date(start)} – {a1date(end)}"


@register.simple_tag
def a1span(start_date, start_time, end_date, end_time):
    """
    A stretch of work that may or may not carry times.

        {% a1span task.start_date task.start_time task.due_date task.due_time %}

        3–12 Sept                 days only, as before
        3 Sept, 09:00–11:30       one day, both times
        3 Sept from 09:00         one day, start only
        3 Sept by 11:30           one day, end only
        3 Sept 09:00 – 5 Sept 17:00   across days, both times

    A tag rather than a filter because it takes four values, and one place
    rather than four templates because a span written three ways is how a
    reader stops trusting any of them.
    """
    if start_date is None and end_date is None:
        return ""

    def clock(value):
        return a1time(value)

    # The whole thing on one day is the common case and reads best said once.
    if start_date and end_date and start_date == end_date:
        day = a1date(start_date)
        if start_time and end_time:
            return f"{day}, {clock(start_time)}–{clock(end_time)}"
        if start_time:
            return f"{day} from {clock(start_time)}"
        if end_time:
            return f"{day} by {clock(end_time)}"
        return day

    if not (start_time or end_time):
        return a1daterange(start_date, end_date)

    left = ""
    if start_date:
        left = a1date(start_date) + (f" {clock(start_time)}" if start_time else "")
    right = ""
    if end_date:
        right = a1date(end_date) + (f" {clock(end_time)}" if end_time else "")

    if left and right:
        return f"{left} – {right}"
    return f"from {left}" if left else f"due {right}"


@register.simple_tag
def a1visitspan(starts, ends):
    """
    A field job's stretch, from two datetimes:

        {% a1visitspan job.scheduled_for job.expected_end %}

        15 Oct from 09:00          one day, the usual case
        15 Oct 09:00 – 17 Oct 17:00   a visit that runs for three days

    A thin wrapper over a1span rather than a fourth way of writing a range.
    A visit stores two moments where a task stores dates and optional times,
    and the difference belongs here and not in four templates.
    """
    if starts is None and ends is None:
        return ""
    starts = _as_local(starts) if starts is not None else None
    ends = _as_local(ends) if ends is not None else None
    return a1span(
        starts.date() if starts else None,
        starts.time() if starts else None,
        ends.date() if ends else None,
        ends.time() if ends else None,
    )


@register.filter
def a1time(value):
    """
    `09:00`. A 24-hour clock, because a crew reading a job on a phone in
    sunlight should not have to parse am/pm, and because the rest of the
    system already writes times this way.
    """
    if value is None:
        return ""
    return value.strftime("%H:%M")


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
def a1coords(value):
    """
    `6.300774, -10.797160` — the fix as it was recorded, from anything
    carrying latitude and longitude.

    A-1 asked to see where somebody signed in, not only whether the system
    approved of it. The verdict alone ("At the office") is a judgement
    nobody can check; the pair of numbers is the evidence behind it, and it
    was being stored on every clock event and shown on no screen at all.

    Six decimal places, which is roughly a tenth of a metre — the precision
    the column is stored at, and well past anything a handset can promise.
    """
    if value is None:
        return ""
    latitude = getattr(value, "latitude", None)
    longitude = getattr(value, "longitude", None)
    if latitude is None or longitude is None:
        return ""
    return f"{float(latitude):.6f}, {float(longitude):.6f}"


@register.filter
def a1mapurl(value):
    """
    A link to the recorded fix on a map.

    Nothing is sent anywhere by rendering this — it is an href, and the
    coordinates travel only if somebody clicks it. That is the point: a
    supervisor asking "where was he when he clocked in" can answer it
    without reading two decimals off a screen and typing them somewhere.
    """
    pair = a1coords(value)
    if not pair:
        return ""
    return f"https://www.google.com/maps?q={pair.replace(' ', '')}"


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


@register.filter
def a1waited(since):
    """
    How long something has been waiting, compactly: `2d 4h`, `1h 50m`, `6m`.

    Django's `timesince` gives "1 hour, 50 minutes", which wraps in a narrow
    column and makes a list of waits impossible to scan. The design
    specification's own mockups use this short form.
    """
    if since is None:
        return ""
    delta = timezone.now() - since
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    minutes, hours = seconds // 60, seconds // 3600
    days = seconds // 86400
    if days:
        return f"{days}d {hours % 24}h"
    if hours:
        return f"{hours}h {minutes % 60:02d}m"
    return f"{minutes}m"
