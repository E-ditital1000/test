"""
The figures behind the dashboard's charts.

Separated from the drawing for the usual reason — a number is worth testing
and an SVG path is not — and because every series here has to obey the same
rule the rest of the system does: counted from source rows at request time,
never from a cached total somebody has to remember to refresh.

Two charts, not six. A dashboard that shows everything is read as wallpaper;
these answer the two questions A-1 actually opens the system asking. Who is
in today, and is the ticket list growing or shrinking.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone


def attendance_week(user, days=7):
    """
    The last `days` working days, each split into worked / late / on leave /
    absent.

    Read through the same services the roll-call uses, so the dashboard and
    the roll-call cannot disagree about a day — which they would within a
    week if this counted rows of its own.
    """
    from accounts.scoping import apply_scope
    from hr import services
    from hr.models import AttendanceDay, Employee

    team = apply_scope(
        Employee.objects.filter(is_active=True),
        user,
        "view_attendance_records",
        own_team_filter=Q(supervisor__user=user),
    )
    headcount = team.count()
    if not headcount:
        return {"days": [], "headcount": 0, "max": 0}

    today = timezone.localdate()
    rest = services_rest_days()
    wanted, cursor = [], today
    while len(wanted) < days:
        if cursor.weekday() not in rest:
            wanted.append(cursor)
        cursor -= timedelta(days=1)
    wanted.reverse()

    rows = {
        (row.employee_id, row.date): row
        for row in AttendanceDay.objects.filter(
            employee__in=team, date__gte=wanted[0], date__lte=wanted[-1]
        )
    }
    on_leave = services.approved_leave_days(
        team=team, since=wanted[0], until=wanted[-1], cap_at_today=False
    )

    out = []
    for day in wanted:
        worked = late = away = 0
        for employee_id in team.values_list("pk", flat=True):
            row = rows.get((employee_id, day))
            state = row.state if row else None
            if state in (AttendanceDay.PRESENT,):
                worked += 1
            elif state == AttendanceDay.LATE:
                late += 1
            elif (employee_id, day) in on_leave:
                away += 1
        absent = headcount - worked - late - away
        out.append(
            {
                "date": day,
                "worked": worked,
                "late": late,
                "leave": away,
                # Never negative: a day with more rows than the current
                # headcount is possible after somebody is deactivated, and a
                # bar below the axis is a drawing bug rather than a fact.
                "absent": max(absent, 0),
            }
        )

    return {"days": out, "headcount": headcount, "max": headcount}


def services_rest_days():
    """The working week, from Settings. Sunday only, for A-1."""
    from hr.leave import rest_days

    return rest_days()


def tickets_fortnight(days=14):
    """
    Raised against closed, by day.

    Two series rather than one net line, because "three in, five out" and
    "nothing happened" both net to minus two and mean entirely different
    things about a working week.
    """
    from crm.models import Ticket

    today = timezone.localdate()
    first = today - timedelta(days=days - 1)
    span = [first + timedelta(days=offset) for offset in range(days)]

    raised = {day: 0 for day in span}
    closed = {day: 0 for day in span}

    for created in Ticket.objects.filter(created_at__date__gte=first).values_list(
        "created_at", flat=True
    ):
        day = timezone.localtime(created).date()
        if day in raised:
            raised[day] += 1

    for closed_at in Ticket.objects.filter(
        closed_at__isnull=False, closed_at__date__gte=first
    ).values_list("closed_at", flat=True):
        day = timezone.localtime(closed_at).date()
        if day in closed:
            closed[day] += 1

    points = [
        {"date": day, "raised": raised[day], "closed": closed[day]} for day in span
    ]
    ceiling = max([p["raised"] for p in points] + [p["closed"] for p in points] + [0])
    total_raised = sum(raised.values())
    total_closed = sum(closed.values())
    return {
        "points": points,
        "max": ceiling,
        "total_raised": total_raised,
        "total_closed": total_closed,
        # The sentence under the chart, decided here. Django has no subtract
        # filter and the arithmetic does not belong in a template anyway.
        "net": abs(total_raised - total_closed),
        "direction": (
            "grew" if total_raised > total_closed
            else "shrank" if total_closed > total_raised
            else "level"
        ),
        "has_data": ceiling > 0,
    }


def sparkline_points(values, width, height, ceiling=None):
    """
    `x,y x,y ...` for an SVG polyline, with the baseline at the bottom.

    Returned as plain numbers so the template does arithmetic nowhere — a
    template that computes coordinates is one nobody can test.
    """
    if not values:
        return ""
    ceiling = ceiling or max(values) or 1
    step = width / max(len(values) - 1, 1)
    return " ".join(
        f"{round(index * step, 1)},{round(height - (value / ceiling) * height, 1)}"
        for index, value in enumerate(values)
    )


# The drawing geometry. Computed here rather than in the template, because a
# template that does arithmetic is one nobody can test — and these are the
# numbers that decide whether a bar tells the truth.

BAR_HEIGHT = 132          # the plot area, in SVG units
BAR_WIDTH = 26
BAR_GAP = 2               # the surface gap between stacked segments
COLUMN_PITCH = 78         # centre-to-centre spacing of the day columns

# Order matters: worked at the bottom, so the eye reads up from the thing
# that went right to the thing that did not.
ATTENDANCE_BANDS = [
    ("worked", "Worked", "var(--viz-good)"),
    ("late", "Late", "var(--viz-warning)"),
    ("leave", "On leave", "var(--viz-neutral)"),
    ("absent", "Absent", "var(--viz-critical)"),
]


def attendance_columns(week):
    """
    One column per day, each a list of segments ready to draw.

    A segment under 3 units high is drawn at 3 so that one person in sixteen
    is still visible; the tooltip carries the real number, and the axis is
    not the thing being read for precision.
    """
    headcount = week["headcount"] or 1
    columns = []
    for day in week["days"]:
        cursor = BAR_HEIGHT
        segments = []
        for key, label, colour in ATTENDANCE_BANDS:
            value = day[key]
            if not value:
                continue
            height = max(round(value / headcount * BAR_HEIGHT, 1), 3)
            cursor -= height
            segments.append(
                {
                    "key": key,
                    "label": label,
                    "colour": colour,
                    "value": value,
                    "y": round(max(cursor, 0), 1),
                    "height": round(height - BAR_GAP if height > BAR_GAP else height, 1),
                }
            )
            cursor -= BAR_GAP
        columns.append(
            {
                "date": day["date"],
                "segments": segments,
                # The column's own x, so the template places it without
                # arithmetic. Django has no multiply filter and adding one to
                # draw a chart would be the wrong place to put the geometry.
                "x": len(columns) * COLUMN_PITCH,
                "total": day["worked"] + day["late"] + day["leave"] + day["absent"],
                "worked": day["worked"],
                "late": day["late"],
                "leave": day["leave"],
                "absent": day["absent"],
            }
        )
    return columns


LINE_WIDTH = 560
LINE_HEIGHT = 120


def ticket_lines(fortnight):
    """Two polylines over one scale. Never two y-axes — see the rule."""
    points = fortnight["points"]
    ceiling = max(fortnight["max"], 1)
    raised = [p["raised"] for p in points]
    closed = [p["closed"] for p in points]
    step = LINE_WIDTH / max(len(points) - 1, 1)
    return {
        "raised": sparkline_points(raised, LINE_WIDTH, LINE_HEIGHT, ceiling),
        "closed": sparkline_points(closed, LINE_WIDTH, LINE_HEIGHT, ceiling),
        "ceiling": ceiling,
        "markers": [
            {
                "x": round(index * step, 1),
                "raised_y": round(LINE_HEIGHT - (p["raised"] / ceiling) * LINE_HEIGHT, 1),
                "closed_y": round(LINE_HEIGHT - (p["closed"] / ceiling) * LINE_HEIGHT, 1),
                "date": p["date"],
                "raised": p["raised"],
                "closed": p["closed"],
            }
            for index, p in enumerate(points)
        ],
    }
