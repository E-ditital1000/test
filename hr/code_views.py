"""
The attendance codes HR prints and sticks on a wall.

What a code proves is narrow and worth being plain about: whoever clocked
had the code in front of them. It does not prove they were at the site — a
printed code is a shared secret, and a photograph of it works exactly as
well as the paper until it expires. That is inherent to printing a code
rather than rotating one on a screen.

So these screens are built around the record rather than the prevention:
every code shows what has been scanned from it, every event carries the code
it came from, and revoking is one click because withdrawal is the only
control that acts immediately.
"""
import io
from datetime import timedelta

import qrcode
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts import audit
from config.pagination import paginate
from accounts.decorators import require_permission

from . import services
from .forms import AttendanceCodeForm
from .models import AttendanceCode


@require_permission("manage_attendance_codes")
def codes(request):
    """Every code, live ones first, each with what has been scanned from it."""
    from django.db.models import Count

    rows = (
        AttendanceCode.objects.annotate(scan_count=Count("events"))
        .select_related("created_by", "revoked_by")
        .order_by("-created_at")
    )
    now = timezone.now()
    live = [code for code in rows if code.status_at(now) == AttendanceCode.ACTIVE]
    dead = [code for code in rows if code.status_at(now) != AttendanceCode.ACTIVE]

    # An expiry inside the next two days is the thing HR must act on before
    # a crew turns up to a code that no longer works.
    soon = now + timedelta(days=2)
    return render(
        request,
        "hr/codes.html",
        {
            "live": paginate(request, live),
            "dead": dead[:25],
            "expiring_soon": [c for c in live if c.expires_at <= soon],
            "form": AttendanceCodeForm(),
        },
    )


@require_permission("manage_attendance_codes")
def code_create(request):
    form = AttendanceCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        code = form.save(commit=False)
        code.created_by = request.user
        code.save()
        audit.record_change(
            actor=request.user,
            action="attendance_code.created",
            target=code,
            after={
                "short_code": code.short_code,
                "location": code.location_name,
                "expires_at": str(code.expires_at),
            },
            reason="Attendance code issued",
        )
        messages.success(
            request,
            f"Code {code.short_code} created for {code.location_name}. "
            "Print it and post it where the crew clocks.",
        )
        return redirect("hr-code-print", pk=code.pk)

    if request.method == "POST":
        messages.error(request, "That code could not be created.")
    return render(request, "hr/code_form.html", {"form": form})


@require_permission("manage_attendance_codes")
def code_print(request, pk):
    """
    The sheet that goes on the wall. Deliberately one code per page, large,
    with the short code under the QR — a camera that will not start in the
    rain is the normal case, not the exception.
    """
    code = get_object_or_404(AttendanceCode, pk=pk)
    return render(
        request,
        "hr/code_print.html",
        {"code": code, "scan_url": request.build_absolute_uri(
            reverse("hr-clock") + f"?code={code.token}"
        )},
    )


@require_permission("manage_attendance_codes")
def code_image(request, pk):
    """
    The QR itself, as a PNG.

    It encodes the clock screen's URL with the token on it, not the bare
    token: a stock phone camera can open a link, and can do nothing at all
    with a UUID. Offline is unaffected — the service worker serves the clock
    screen from the device, and the token rides in on the query string.
    """
    code = get_object_or_404(AttendanceCode, pk=pk)

    target = request.build_absolute_uri(reverse("hr-clock") + f"?code={code.token}")
    image = qrcode.make(target, box_size=10, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    response = HttpResponse(buffer.getvalue(), content_type="image/png")
    # A code's QR never changes, but a revoked one must not sit in a proxy.
    response["Cache-Control"] = "private, no-store"
    return response


@require_permission("manage_attendance_codes")
def code_revoke(request, pk):
    """
    Withdrawal is immediate and permanent — there is no un-revoke, because a
    code that has leaked stays leaked. HR prints a new one.
    """
    code = get_object_or_404(AttendanceCode, pk=pk)
    if request.method != "POST":
        return redirect("hr-codes")

    if code.revoked_at is not None:
        messages.info(request, f"{code.short_code} was already withdrawn.")
        return redirect("hr-codes")

    before = {"revoked_at": None}
    code.revoked_at = timezone.now()
    code.revoked_by = request.user
    code.save(update_fields=["revoked_at", "revoked_by"])

    audit.record_change(
        actor=request.user,
        action="attendance_code.revoked",
        target=code,
        before=before,
        after={"revoked_at": str(code.revoked_at)},
        reason=request.POST.get("reason", ""),
    )
    messages.success(
        request,
        f"{code.short_code} withdrawn. It stops working immediately; the "
        f"{code.events.count()} event(s) already recorded from it are unaffected.",
    )
    return redirect("hr-codes")


@require_permission("manage_attendance_codes")
def office_location(request):
    """
    Setting the place people clock. HR's, because HR already owns the codes
    posted at that place; an Admin holds it too.

    Until this is set, every clock event records a position and is judged
    against nothing — somebody can clock in from home and the record stores
    the coordinates of their living room.
    """
    from config.models import PolicySetting

    from .forms import OfficeLocationForm

    current = services.office_location()
    form = OfficeLocationForm(
        request.POST or None,
        initial={
            "latitude": current[0] if current else None,
            "longitude": current[1] if current else None,
            "radius_m": current[2] if current else 200,
        },
    )

    if request.method == "POST" and form.is_valid():
        values = {
            PolicySetting.OFFICE_LATITUDE: form.cleaned_data["latitude"],
            PolicySetting.OFFICE_LONGITUDE: form.cleaned_data["longitude"],
            PolicySetting.GEOFENCE_RADIUS_M: form.cleaned_data["radius_m"],
        }
        for key, value in values.items():
            PolicySetting.objects.update_or_create(
                key=key, defaults={"value": "" if value is None else str(value)}
            )
        audit.record_change(
            actor=request.user,
            action="attendance.office_location_set",
            target=PolicySetting.objects.get(key=PolicySetting.OFFICE_LATITUDE),
            before=None,
            after={str(k): str(v) for k, v in values.items()},
            reason="Where staff may clock in and out from.",
        )
        if form.cleaned_data["latitude"] is None:
            messages.success(
                request,
                "Office location cleared. Clock events record a position "
                "again but are not judged against anywhere.",
            )
        else:
            messages.success(
                request,
                f"Office location saved. Staff may clock within "
                f"{form.cleaned_data['radius_m']} m of it, plus their phone's "
                "own margin of error.",
            )
        return redirect("hr-office-location")

    return render(
        request,
        "hr/office_location.html",
        {"form": form, "current": current, "tab_active": "office_location"},
    )
