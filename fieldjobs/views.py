"""
Field Jobs — the technician's mobile surface, plus the office screen that
puts work on their phone.

Connectivity is assumed absent, not present. The assessment is filled in
entirely on the device and reaches the server as one payload carrying the
client UUID it was created with, so a resubmission after a dropped
connection — or from a second device — resolves to the row that already
exists instead of creating a second one.

The assessment form itself is generated from the service type's question set
in Settings. Nothing about it is hardcoded here.
"""
import base64
import binascii
import json
import uuid

from django.contrib import messages
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.decorators import require_permission, user_has_permission
from config.references import next_reference
from projects.models import Project

from .forms import FieldJobForm
from .models import (
    Assessment,
    AssessmentAnswer,
    AssessmentPhoto,
    CheckIn,
    FieldJob,
    FieldJobCrew,
)

# A phone camera frame held as a data URL is large; anything past this is a
# sign something has gone wrong rather than a genuine site photo.
MAX_PHOTO_BYTES = 4 * 1024 * 1024
MAX_PHOTOS = 12


# --------------------------------------------------------------------------
# The technician's own job list
# --------------------------------------------------------------------------

@require_permission("view_own_job_list")
def my_jobs(request):
    """
    Today's jobs, with the next one promoted and the rest kept quiet. Scoped
    to this technician by construction — there is no way to ask for anybody
    else's list from here.
    """
    now = timezone.now()
    today = timezone.localdate()

    # A job belongs on your phone if you lead it or you are on its crew.
    mine = (
        FieldJob.objects.filter(
            Q(assigned_to=request.user) | Q(crew__employee__user=request.user)
        )
        .distinct()
        .select_related("customer", "site", "service_type", "project")
        .prefetch_related("crew__employee__user")
    )
    todays = list(mine.filter(scheduled_for__date=today).order_by("scheduled_for"))
    upcoming = list(
        mine.filter(scheduled_for__date__gt=today).order_by("scheduled_for")[:5]
    )

    outstanding = [job for job in todays if job.state != FieldJob.COMPLETED]

    # A technician works from this screen. A task they are given that only
    # lives on a project page in the office is a task they will never do.
    from projects.models import Task

    my_tasks = list(
        Task.objects.for_person(request.user).open().select_related("project")[:6]
    )

    return render(
        request,
        "fieldjobs/my_jobs.html",
        {
            "tasks": my_tasks,
            "overdue_tasks": sum(1 for t in my_tasks if t.is_overdue),
            "next_job": outstanding[0] if outstanding else None,
            "later_today": outstanding[1:],
            "done_today": [job for job in todays if job.state == FieldJob.COMPLETED],
            "upcoming": upcoming,
            "today": today,
            "tab_active": "field",
        },
    )


def _own_job(request, pk):
    """
    A technician only ever reaches a job they are on — as its lead or as
    crew. Anyone else gets a 404 rather than a refusal, because the
    existence of another crew's job is not theirs to learn.
    """
    return get_object_or_404(
        FieldJob.objects.filter(
            Q(assigned_to=request.user) | Q(crew__employee__user=request.user)
        )
        .distinct()
        .select_related("customer", "site", "service_type", "project")
        .prefetch_related("crew__employee__user"),
        pk=pk,
    )


def _crew_rows(job, viewer):
    """
    Everyone on this visit, in the order they matter, each carrying their own
    arrival.

    Who is on site and who has actually turned up are the same question asked
    twice, and they were being answered in two places — a crew list that knew
    nothing about arrivals, and a check-in list nothing rendered. One row per
    person answers both.
    """
    arrivals = {}
    for event in job.check_ins.select_related("technician").order_by("device_timestamp"):
        # First arrival, not the latest. Someone who checks in twice arrived
        # once.
        arrivals.setdefault(event.technician_id, event)

    rows = [{
        "person": job.assigned_to,
        "role": "Lead",
        "is_you": job.assigned_to_id == viewer.pk,
        "arrival": arrivals.get(job.assigned_to_id),
    }]
    for member in job.crew.all():
        person = member.employee.user
        rows.append({
            "person": person,
            "role": "Crew",
            "is_you": person.pk == viewer.pk,
            "arrival": arrivals.get(person.pk),
        })
    return rows


@require_permission("view_own_job_list")
def job_detail(request, pk):
    job = _own_job(request, pk)
    assessment = job.assessments.order_by("-device_timestamp").first()
    is_lead = job.is_led_by(request.user)
    crew_rows = _crew_rows(job, request.user)
    return render(
        request,
        "fieldjobs/job_detail.html",
        {
            "job": job,
            "crew": job.crew.all(),
            "crew_rows": crew_rows,
            "arrived_count": sum(1 for row in crew_rows if row["arrival"]),
            "is_lead": is_lead,
            # My own check-in, not the lead's — each person on site checks in
            # for themselves.
            "check_in": job.check_ins.filter(technician=request.user)
            .order_by("-device_timestamp").first(),
            "assessment": assessment,
            # Crew can be on site and check in; the assessment is the lead's.
            "can_assess": is_lead and user_has_permission(request.user, "submit_assessment"),
            "tab_active": "field",
        },
    )


@require_permission("gps_check_in")
def check_in(request, pk):
    """
    GPS check-in on arrival. Captured at the event only, never continuously,
    and a missing fix records the check-in anyway rather than blocking it.
    """
    job = _own_job(request, pk)
    if request.method != "POST":
        return redirect("fieldjobs-job-detail", pk=job.pk)

    client_uuid = request.POST.get("client_uuid") or str(uuid.uuid4())
    if CheckIn.objects.filter(client_uuid=client_uuid).exists():
        messages.info(request, "Already checked in — nothing was duplicated.")
        return redirect("fieldjobs-job-detail", pk=job.pk)
    if job.check_ins.filter(technician=request.user).exists():
        messages.info(request, "You are already checked in on this job.")
        return redirect("fieldjobs-job-detail", pk=job.pk)

    def decimal_or_none(name):
        raw = (request.POST.get(name) or "").strip()
        return raw or None

    latitude = decimal_or_none("latitude")
    CheckIn.objects.create(
        field_job=job,
        technician=request.user,
        client_uuid=client_uuid,
        device_timestamp=parse_datetime(request.POST.get("device_timestamp") or "") or timezone.now(),
        latitude=latitude,
        longitude=decimal_or_none("longitude"),
        accuracy_m=(request.POST.get("accuracy_m") or None),
        location_unavailable=request.POST.get("location_unavailable") == "1" or latitude is None,
    )
    if job.state == FieldJob.SCHEDULED:
        job.state = FieldJob.IN_PROGRESS
        job.save(update_fields=["state"])

    messages.success(request, "Checked in on site.")
    return redirect("fieldjobs-job-detail", pk=job.pk)


# --------------------------------------------------------------------------
# The assessment
# --------------------------------------------------------------------------

@require_permission("submit_assessment")
def assessment_form(request, pk):
    """
    Renders the question set for this job's service type as a multi-step
    form. The questions come from Settings; adding a service type is
    configuration, so nothing here knows what any of them ask.
    """
    job = _own_job(request, pk)
    if not job.is_led_by(request.user):
        # Crew are on the visit but do not report on it. Say which, so the
        # refusal is actionable rather than mysterious.
        messages.error(
            request,
            f"{job.assigned_to.get_full_name() or job.assigned_to.email} leads this "
            "job and submits its assessment. You can check in and work on it.",
        )
        return redirect("fieldjobs-job-detail", pk=job.pk)

    questions = list(job.service_type.active_questions())

    # Technician answers and the client's own responses are captured as two
    # labelled groups, never mixed together.
    technician_questions = [q for q in questions if q.respondent == "technician"]
    client_questions = [q for q in questions if q.respondent == "client"]

    # Chunked so a low-end phone shows a few questions at a time rather than
    # one long scroll. Photos and review are always the last two steps.
    chunk = 3
    steps = []
    for group, label in ((technician_questions, "Technician"), (client_questions, "Client")):
        for index in range(0, len(group), chunk):
            steps.append({"label": label, "questions": group[index:index + chunk]})

    existing = job.assessments.filter(state=Assessment.SUBMITTED).first()
    return render(
        request,
        "fieldjobs/assessment.html",
        {
            "job": job,
            "steps": steps,
            "total_steps": len(steps) + 2,  # + photos + review
            "required_count": sum(1 for q in questions if q.is_required),
            "question_count": len(questions),
            "already_submitted": existing,
            "tab_active": "field",
        },
    )


@require_permission("submit_assessment")
@transaction.atomic
def assessment_submit(request, pk):
    """
    Accepts the whole assessment as one payload.

    `client_uuid` was generated on the device before the technician started
    answering, so this is idempotent: the same assessment pushed twice, or
    from two devices, creates one row. That is what lets the phone retry
    blindly when signal returns.
    """
    job = _own_job(request, pk)
    if not job.is_led_by(request.user):
        # Enforced server-side, not merely hidden: a crew member's device
        # could post this directly.
        return JsonResponse(
            {"ok": False, "message": "Only the job's lead submits its assessment."},
            status=403,
        )
    if request.method != "POST":
        return redirect("fieldjobs-assessment", pk=job.pk)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "message": "That submission could not be read."}, status=400)

    client_uuid = (payload.get("client_uuid") or "").strip()
    if not client_uuid:
        return JsonResponse(
            {"ok": False, "message": "This assessment has no client reference and cannot be synced."},
            status=400,
        )

    existing = Assessment.objects.filter(client_uuid=client_uuid).first()
    if existing is not None:
        return JsonResponse(
            {"ok": True, "duplicate": True, "message": "Already submitted — nothing was duplicated.",
             "redirect": job.get_absolute_url() if hasattr(job, "get_absolute_url") else ""}
        )

    device_timestamp = parse_datetime(payload.get("device_timestamp") or "") or timezone.now()
    if timezone.is_naive(device_timestamp):
        device_timestamp = timezone.make_aware(device_timestamp)

    location = payload.get("location") or {}
    try:
        assessment = Assessment.objects.create(
            field_job=job,
            service_type=job.service_type,
            technician=request.user,
            client_uuid=client_uuid,
            device_timestamp=device_timestamp,
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            accuracy_m=location.get("accuracy_m"),
            location_unavailable=bool(location.get("unavailable")) or location.get("latitude") is None,
            state=Assessment.SUBMITTED,
            submitted_at=timezone.now(),
            notes=(payload.get("notes") or "")[:5000],
        )
    except IntegrityError:
        # Two retries raced each other; the other one won, which is fine.
        return JsonResponse({"ok": True, "duplicate": True, "message": "Already submitted."})

    questions = {q.pk: q for q in job.service_type.questions.all()}
    for raw in payload.get("answers", []):
        question = questions.get(raw.get("question_id"))
        if question is None:
            continue
        AssessmentAnswer.objects.create(
            assessment=assessment,
            question=question,
            # Copied at submission, so retiring or rewording the question
            # later never changes what this assessment says was asked.
            question_text=question.text,
            respondent=question.respondent,
            value_text=str(raw.get("value", ""))[:5000],
        )

    for raw in (payload.get("photos") or [])[:MAX_PHOTOS]:
        image = _decode_photo(raw.get("data_url") or "")
        if image is None:
            continue
        photo_uuid = (raw.get("client_uuid") or "").strip() or str(uuid.uuid4())
        if AssessmentPhoto.objects.filter(client_uuid=photo_uuid).exists():
            continue
        AssessmentPhoto.objects.create(
            assessment=assessment,
            client_uuid=photo_uuid,
            label=(raw.get("label") or "Site photo")[:150],
            image=image,
        )

    # Submitting starts the approval trail — the office side reads this.
    assessment.record_decision(decision="submitted", actor=request.user)

    if job.state != FieldJob.COMPLETED:
        job.state = FieldJob.COMPLETED
        job.completed_at = timezone.now()
        job.save(update_fields=["state", "completed_at"])

    return JsonResponse(
        {"ok": True, "message": "Assessment submitted.", "reference": job.reference}
    )


def _decode_photo(data_url):
    """A `data:image/...;base64,...` string from the device, as a file."""
    if not data_url.startswith("data:image/"):
        return None
    try:
        header, encoded = data_url.split(",", 1)
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not raw or len(raw) > MAX_PHOTO_BYTES:
        return None
    extension = "png" if "png" in header else "jpg"
    return ContentFile(raw, name=f"{uuid.uuid4()}.{extension}")


# --------------------------------------------------------------------------
# The office side — putting a job on a phone
# --------------------------------------------------------------------------

@require_permission("schedule_field_job")
@transaction.atomic
def schedule(request, project_pk=None):
    project = get_object_or_404(Project, pk=project_pk) if project_pk else None
    form = FieldJobForm(request.POST or None, project=project)

    if request.method == "POST" and form.is_valid():
        job = form.save(commit=False)
        if project is not None:
            # The lineage carries forward: this visit belongs to the same job
            # as the ticket it started as.
            job.project = project
            job.ticket = project.ticket
            job.customer = project.customer
            job.service_type = project.service_type
            job.job_ref = project.job_ref
        job.reference = next_reference(FieldJob, "FJ")
        job.save()

        crew = form.cleaned_data.get("crew") or []
        for member in crew:
            # The lead is already on the job; adding them again would put
            # their name on it twice.
            if member.user_id == job.assigned_to_id:
                continue
            FieldJobCrew.objects.create(
                field_job=job, employee=member, assigned_by=request.user
            )

        who = job.assigned_to.get_full_name() or job.assigned_to.email
        extra = len([m for m in crew if m.user_id != job.assigned_to_id])
        messages.success(
            request,
            f"{job.reference} scheduled for {who}"
            + (f" with {extra} other{'s' if extra != 1 else ''} on site." if extra else "."),
        )
        if project is not None:
            return redirect("projects-detail", pk=project.pk)
        return redirect("fieldjobs-schedule")

    return render(
        request,
        "fieldjobs/schedule_form.html",
        {"form": form, "project": project},
    )
