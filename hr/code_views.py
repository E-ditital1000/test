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
