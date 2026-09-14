"""
My profile: the one place a person looks after their own account.

Every screen here is open to anybody signed in and acts only on the person
signed in. There is no pk in any of these URLs, so there is no way to reach
somebody else's profile by editing the address bar. That is why these views
carry `login_required` rather than a permission code: holding a profile is
not a privilege, it is what having an account means.
"""
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from config.pagination import paginate

from . import audit
from .forms import OwnPasswordChangeForm, ProfileForm, ProfilePhotoForm
from .models import AuditEntry, Profile

# What a person sees for the actions most likely to appear against them.
# Anything not listed falls back to a readable version of its code.
ACTION_LABELS = {
    "profile.updated": "You updated your profile",
    "profile.photo_changed": "You changed your photo",
    "profile.photo_removed": "You removed your photo",
    "profile.password_changed": "You changed your password",
    "user.created": "Your account was created",
    "user.role_assigned": "A role was added to your account",
    "user.role_revoked": "A role was removed from your account",
    "user.deactivated": "Your account was deactivated",
    "employee.onboarded": "You were taken on",
    "employee.created": "Your employee record was created",
    "employee.updated": "Your employee record was updated",
    "access.refused": "A screen refused you access",
}


def _profile_for(user):
    profile, _created = Profile.objects.get_or_create(user=user)
    return profile


def _employee_for(user):
    from hr.models import Employee

    return (
        Employee.objects.select_related("supervisor__user")
        .filter(user=user)
        .first()
    )


def _header_context(request, profile, active):
    user = request.user
    return {
        "profile": profile,
        "employee": _employee_for(user),
        "roles": list(user.user_roles.select_related("role").values_list("role__name", flat=True)),
        "profile_tab": active,
    }


@login_required
def profile_details(request):
    profile = _profile_for(request.user)
    form = ProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        changed = sorted(form.changed_data)
        if changed:
            form.save()
            # Field names only, never the values. The audit log is read by
            # administrators, and a date of birth or a home address is none
            # of their business just because it was edited.
            audit.record_change(
                actor=request.user,
                action="profile.updated",
                target=profile,
                after={"fields": changed},
            )
            messages.success(request, "Profile saved.")
        else:
            messages.info(request, "Nothing had changed, so there was nothing to save.")
        return redirect("profile")
    context = _header_context(request, profile, "details")
    context.update({"form": form, "photo_form": ProfilePhotoForm()})
    return render(request, "accounts/profile/details.html", context)


@login_required
@require_POST
def profile_photo(request):
    profile = _profile_for(request.user)
    form = ProfilePhotoForm(request.POST, request.FILES)
    if not form.is_valid():
        for error in form.errors.get("photo", []):
            messages.error(request, error)
        return redirect("profile")

    old_name = profile.photo.name if profile.photo else ""
    profile.photo.save("photo.jpg", form.cleaned_data["photo"], save=True)
    _delete_file(profile.photo.storage, old_name)
    audit.record_change(actor=request.user, action="profile.photo_changed", target=profile)
    messages.success(request, "Photo updated.")
    return redirect("profile")


@login_required
@require_POST
def profile_photo_remove(request):
    profile = _profile_for(request.user)
    if profile.photo:
        storage, old_name = profile.photo.storage, profile.photo.name
        profile.photo = ""
        profile.save(update_fields=["photo", "updated_at"])
        _delete_file(storage, old_name)
        audit.record_change(actor=request.user, action="profile.photo_removed", target=profile)
        messages.success(request, "Photo removed.")
    return redirect("profile")


def _delete_file(storage, name):
    """
    A replaced photo is deleted, not orphaned. A failure here must not undo
    a change that has already been saved, so it is swallowed.
    """
    if not name:
        return
    try:
        storage.delete(name)
    except Exception:
        pass


@login_required
def profile_security(request):
    profile = _profile_for(request.user)
    form = OwnPasswordChangeForm(user=request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        # Keeps this session signed in. Every other session was signed with
        # the old password hash, so those end on their next request.
        update_session_auth_hash(request, form.user)
        audit.record_change(actor=request.user, action="profile.password_changed", target=request.user)
        messages.success(request, "Password changed. Any other device signed in as you has been signed out.")
        return redirect("profile-security")
    context = _header_context(request, profile, "security")
    context["form"] = form
    return render(request, "accounts/profile/security.html", context)


@login_required
def profile_activity(request):
    user = request.user
    profile = _profile_for(user)
    context = _header_context(request, profile, "activity")

    about_me = Q(actor=user)
    about_me |= Q(target_type="accounts.User", target_id=str(user.pk))
    about_me |= Q(target_type="accounts.Profile", target_id=str(profile.pk))
    if context["employee"]:
        about_me |= Q(target_type="hr.Employee", target_id=str(context["employee"].pk))

    entries = paginate(request, AuditEntry.objects.filter(about_me).select_related("actor"))
    for entry in entries:
        entry.label = ACTION_LABELS.get(entry.action) or entry.action.replace("_", " ").replace(".", ": ").capitalize()
        entry.by_someone_else = entry.actor_id is not None and entry.actor_id != user.pk
    context["entries"] = entries
    return render(request, "accounts/profile/activity.html", context)
