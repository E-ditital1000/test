# A field job that runs for more than a day

A-1: *"Field job doesn't have duration range. e.g. from October 15 —
October 17."*

A `FieldJob` knew `scheduled_for` and nothing else. Every screen therefore
treated a visit as a moment, and a three-day installation was booked as
"15 Oct 09:00" with the other two days existing only in somebody's head.

## What was added

One nullable field, `expected_end`, beside `scheduled_for`. Blank means the
job is the day it starts, which is most of them — a form that insisted on
an end for a two-hour call-out would be answered with an invented time.

Both are datetimes rather than dates, because `scheduled_for` already was
one and a crew needs to know they are expected at nine.

## Why it is not only a display change

The field on its own would have been cosmetic. The thing that made this
worth doing is `FieldJobQuerySet.on_day`:

```python
def on_day(self, day):
    """Every visit that occupies this day — starting, ending or spanning."""
    return self.filter(
        Q(scheduled_for__date=day)
        | Q(scheduled_for__date__lte=day, expected_end__date__gte=day)
    )
```

Both the office board and the technician's own **Today's jobs** filtered on
`scheduled_for__date = today`. Under that rule, a crew on the middle day of
their own three-day job open the app and are told *"No jobs assigned
today"*, and the office board shows nothing booked while three people are
standing on a roof in Ganta.

So the range and the filter are one change. Adding the field without
`on_day` would have printed a longer date on a card and left the screens
lying on days two and three.

## Where it shows

| Screen | Reads |
|---|---|
| Schedule board, *When* | `15 Oct 09:00 – 17 Oct 17:00` |
| Job detail, *Runs* | the same span, in place of a bare start time |
| Today's jobs, hero card | `· on site until 17 Oct` |
| Today's jobs, later/upcoming | `· until 17 Oct` |

All of them go through `{% a1visitspan %}`, which is a thin wrapper over the
existing `a1span` rather than a fourth way of writing a date range. A span
written three ways is how a reader stops trusting any of them.

## A bug found on the way

`FieldJobForm` rendered `scheduled_for` with a plain `DateTimeInput`. A
`datetime-local` control will not display Django's localised rendering of a
datetime, so **the edit screen came up with the schedule blank** — an Admin
opening it to change the technician would have cleared the date by saving.
Both datetime fields now carry `format="%Y-%m-%dT%H:%M"`, and
`AdminCorrectsAVisit.test_the_edit_screen_shows_the_time_the_visit_is_already_booked_for`
holds it.
