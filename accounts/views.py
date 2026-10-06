"""
Auth and the Settings module. Every screen here is gated by a permission
code from the frozen registry, and every mutation goes through
`accounts.services` so the section 5 guard rails cannot be bypassed.
"""
from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from config.pagination import paginate

from . import audit, services
from .decorators import require_permission
from .forms import (
    EmailLoginForm,
    ForcedPasswordResetForm,
    RoleForm,
    UserForm,
    grouped_permissions,
)
from .models import SCOPE_ALL, AuditEntry, Role, RolePermission

User = get_user_model()


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard-index")

    form = EmailLoginForm(request.POST or None)
    locked, lock_message = False, ""

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        password = form.cleaned_data["password"]
        account = User.objects.filter(email__iexact=email).first()

        if account and account.is_locked_out():
            remaining = int((account.locked_until - timezone.now()).total_seconds() // 60) + 1
            # The locked panel is its own designed state rather than a red
            # flash message. It says nothing about whether the address
            # exists — the same screen appears either way.
            locked = True
            lock_message = (
                "This account is locked after {} failed sign-ins. "
                "It unlocks in {} minute(s), or an Admin can reset your password now.".format(
                    django_settings.LOGIN_LOCKOUT_THRESHOLD, remaining
                )
            )
        else:
            user = authenticate(request, email=email, password=password)
            if user is not None:
                _reset_lockout(user)
                login(request, user)
                request.session.set_expiry(django_settings.SESSION_COOKIE_AGE)
                if user.must_reset_password:
                    return redirect("password-reset-required")
                return redirect("dashboard-index")
            _register_failure(account)
            messages.error(request, "Email or password is incorrect.")

    return render(
        request,
        "accounts/login.html",
        {
            "form": form,
            "locked": locked,
            "lock_message": lock_message,
            "lockout_threshold": django_settings.LOGIN_LOCKOUT_THRESHOLD,
        },
    )


def _register_failure(account):
    """
    Lockout counts failures per account. The message shown to the person
    typing is identical whether or not the account exists.
    """
    if account is None:
        return
    account.failed_login_attempts += 1
    if account.failed_login_attempts >= django_settings.LOGIN_LOCKOUT_THRESHOLD:
        account.locked_until = timezone.now() + timedelta(
            minutes=django_settings.LOGIN_LOCKOUT_MINUTES
        )
        account.failed_login_attempts = 0
    account.save(update_fields=["failed_login_attempts", "locked_until"])


def _reset_lockout(user):
    if user.failed_login_attempts or user.locked_until:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["failed_login_attempts", "locked_until"])


def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def password_reset_required(request):
    form = ForcedPasswordResetForm(user=request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        request.user.must_reset_password = False
        request.user.save(update_fields=["must_reset_password"])
        update_session_auth_hash(request, request.user)
        messages.success(request, "Password updated.")
        return redirect("dashboard-index")
    return render(request, "accounts/password_reset_required.html", {"form": form})


# --------------------------------------------------------------------------
# Settings - users
# --------------------------------------------------------------------------

@require_permission("manage_users")
def settings_users(request):
    query = request.GET.get("q", "").strip()
    users = User.objects.prefetch_related("user_roles__role").order_by("first_name", "email")
    if query:
        users = users.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
        )
    # Administrators are peers: one does not hold another's account. Marked
    # per row so the two actions that are refused are not offered, rather
    # than offered and then refused.
    from hr.models import Employee

    administrators = services.admin_user_ids()
    # Accounts with no employment record behind them. One carrying a staff
    # role and nothing on the register cannot clock in, is on no roll-call
    # and in no report — and until this said so, looked complete.
    on_register = set(Employee.objects.values_list("user_id", flat=True))
    page = paginate(request, users)
    for row in page:
        row.is_peer_administrator = (
            row.pk in administrators
            and row.pk != request.user.pk
            and not request.user.is_superuser
        )
        row.is_on_register = row.pk in on_register

    return render(
        request,
        "accounts/settings_users.html",
        {
            "users": page,
            "query": query,
            "roles": Role.objects.all(),
            # "Nothing matches" must say how many records exist in total,
            # so an empty result never reads as an empty system.
            "total_users": User.objects.count(),
        },
    )


@require_permission("manage_users")
@transaction.atomic
def user_edit(request, pk=None):
    instance = get_object_or_404(User, pk=pk) if pk else None
    form = UserForm(request.POST or None, instance=instance)
    if instance and request.method != "POST":
        form.fields["roles"].initial = Role.objects.filter(user_roles__user=instance)

    if request.method == "POST" and form.is_valid():
        tracked = ["first_name", "last_name", "email", "is_active"]
        before = audit.snapshot(instance, fields=tracked)
        user = form.save(commit=False)
        temporary = None
        if instance is None:
            user.username = form.cleaned_data["email"]
            # An admin never chooses somebody's lasting password: a
            # temporary one is issued and the forced reset replaces it.
            temporary = services.temporary_password()
            user.set_password(temporary)
            user.must_reset_password = True
        user.save()

        # The register, in the same act. Half a person — an account carrying
        # the Employee role with no employment record behind it — is what
        # happens when this is a second screen somebody has to know about.
        on_register = _put_on_the_register(request, user, form)

        _sync_roles(request, user, form.cleaned_data["roles"])
        audit.record_change(
            actor=request.user,
            action="user.created" if instance is None else "user.updated",
            target=user,
            before=before,
            after=audit.snapshot(user, fields=tracked),
            reason=request.POST.get("reason", ""),
        )
        register = " They are on the staff register as {}.".format(on_register.staff_id) if on_register else ""
        if temporary:
            messages.success(
                request,
                "Account created.{} Temporary password: {} - the user must "
                "change it at first login.".format(register, temporary),
                extra_tags="sticky",
            )
        else:
            messages.success(request, "Account updated." + register)
        return redirect("settings-users")

    return render(request, "accounts/user_form.html", {"form": form, "instance": instance})


def _put_on_the_register(request, user, form):
    """
    Create the employment record alongside the account, where the person
    filling this in said there should be one.

    The HR screen does the same thing through `onboard_employee`, which also
    makes the account; here the account exists already, so only the register
    half is written — the same fields, the same rules, one transaction with
    the account above it.
    """
    from hr.models import Employee

    if not form.cleaned_data.get("is_employee"):
        return None
    if Employee.objects.filter(user=user).exists():
        return None

    employee = Employee.objects.create(
        user=user,
        staff_id=form.cleaned_data["staff_id"].strip(),
        job_title=form.cleaned_data.get("job_title", "").strip(),
        department=form.cleaned_data.get("department"),
        phone=form.cleaned_data.get("phone", "").strip(),
        supervisor=form.cleaned_data.get("supervisor"),
        start_date=form.cleaned_data.get("start_date"),
    )
    audit.record_change(
        actor=request.user,
        action="employee.onboarded",
        target=employee,
        before=None,
        after={"staff_id": employee.staff_id, "email": user.email},
        reason="Added to the register with the account",
    )
    return employee


def _sync_roles(request, user, selected_roles):
    current = set(Role.objects.filter(user_roles__user=user))
    selected = set(selected_roles)
    for role in selected - current:
        services.assign_role(actor=request.user, user=user, role=role)
    for role in current - selected:
        services.revoke_role(actor=request.user, user=user, role=role)


@require_permission("manage_users")
def user_deactivate(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        try:
            services.deactivate_user(
                actor=request.user, user=user, reason=request.POST.get("reason", "")
            )
            messages.success(request, "{} deactivated.".format(user))
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        return redirect("settings-users")
    return render(
        request,
        "accounts/confirm.html",
        {
            "title": "Deactivate {}?".format(user),
            "body": "The account keeps all of its history and can be reactivated.",
            "action_label": "Deactivate",
            "cancel_url": reverse("settings-users"),
        },
    )


@require_permission("manage_users")
def user_temporary_password(request, pk):
    """
    The way back in for somebody who forgot their password or locked
    themselves out. There is no emailed reset link: staff sign in with work
    addresses that are not all read, and a link sent to an unread inbox
    helps nobody on site. A person asks an administrator instead, in person
    or by phone, and is handed a password that works once.
    """
    user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        try:
            temporary = services.issue_temporary_password(
                actor=request.user, user=user, reason=request.POST.get("reason", "")
            )
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, getattr(exc, "messages", [str(exc)])[0])
            return redirect("settings-users")
        messages.success(
            request,
            "New temporary password for {}: {} - they must change it when they "
            "next sign in. Any device they were signed in on has been signed "
            "out.".format(user, temporary),
            extra_tags="sticky",
        )
        return redirect("settings-users")
    return render(
        request,
        "accounts/confirm.html",
        {
            "title": "Issue a temporary password for {}?".format(user),
            "body": (
                "Their current password stops working at once, any device they "
                "are signed in on is signed out, and a sign-in lockout is "
                "cleared. The new password is shown to you once; give it to "
                "them directly. They choose their own at next sign-in."
            ),
            "action_label": "Issue temporary password",
            "cancel_url": reverse("settings-users"),
        },
    )


# --------------------------------------------------------------------------
# Settings - roles
# --------------------------------------------------------------------------

@require_permission("manage_roles")
def settings_roles(request):
    roles = Role.objects.prefetch_related("permissions").order_by("-is_system", "name")
    return render(request, "accounts/settings_roles.html", {"roles": roles})


@require_permission("manage_roles")
def role_edit(request, pk=None):
    role = get_object_or_404(Role, pk=pk) if pk else None
    form = RoleForm(request.POST or None, instance=role)

    current = {}
    if role:
        current = {
            rp.permission.code: rp.scope
            for rp in RolePermission.objects.filter(role=role).select_related("permission")
        }

    if request.method == "POST" and form.is_valid():
        grants = [
            (code, request.POST.get("scope__" + code, SCOPE_ALL))
            for code in request.POST.getlist("permissions")
        ]
        try:
            if role is None:
                services.create_role(
                    actor=request.user, name=form.cleaned_data["name"], grants=grants
                )
                messages.success(request, "Role created.")
            else:
                if form.cleaned_data["name"] != role.name:
                    role.name = form.cleaned_data["name"]
                    role.save(update_fields=["name"])
                services.set_role_permissions(
                    actor=request.user,
                    role=role,
                    grants=grants,
                    reason=request.POST.get("reason", ""),
                )
                messages.success(request, "Role updated.")
            return redirect("settings-roles")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, getattr(exc, "messages", [str(exc)])[0])
            current = {code: scope for code, scope in grants}

    return render(
        request,
        "accounts/role_form.html",
        {
            "form": form,
            "role": role,
            "permission_groups": grouped_permissions(current),
            "scopes": [
                (SCOPE_ALL, "All"),
                ("own_team", "Own team"),
                ("own_projects", "Own projects"),
            ],
        },
    )


# --------------------------------------------------------------------------
# Settings - audit log
# --------------------------------------------------------------------------

@require_permission("view_audit_log")
def audit_log(request):
    query = request.GET.get("q", "").strip()
    entries = AuditEntry.objects.select_related("actor")
    if query:
        entries = entries.filter(
            Q(action__icontains=query)
            | Q(target_type__icontains=query)
            | Q(reason__icontains=query)
            | Q(actor__email__icontains=query)
        )
    return render(
        request,
        "accounts/audit_log.html",
        {
            # Was a [:300] slice, which hid everything past it with no
            # way to reach it. The log is the one place that must not do that.
            "entries": paginate(request, entries),
            "query": query,
            "total_entries": AuditEntry.objects.count(),
        },
    )

# --------------------------------------------------------------------------
# Error surfaces
# --------------------------------------------------------------------------

def permission_denied(request, exception=None):
    """
    The designed refusal. `require_permission` raises PermissionDenied with
    the missing code in its message; this turns that into a screen that says
    which permission is missing and who can grant it, rather than a stock
    403 the user can only escalate by telephone.
    """
    from django.shortcuts import render as _render

    from .permission_registry import PERMISSIONS

    message = str(exception or "")
    code = message.split("missing permission:")[-1].strip() if "missing permission:" in message else ""
    label = next((desc for c, _m, desc in PERMISSIONS if c == code), "")

    role_names = ""
    if request.user.is_authenticated:
        role_names = ", ".join(
            request.user.user_roles.values_list("role__name", flat=True)
        )
        # The refusal screen tells the user this is on the record, so it has
        # to actually be on the record. A refused request is also the most
        # useful thing in the log during a security review.
        audit.record_change(
            actor=request.user,
            action="access.refused",
            target=request.user,
            before=None,
            after={"path": request.path, "permission": code},
            reason="Server refused: missing permission",
        )

    return _render(
        request,
        "403.html",
        {
            "missing_permission": label or code or "the one this screen requires",
            "permission_code": code,
            "role_names": role_names,
        },
        status=403,
    )


def server_error(request):
    """
    The designed 500. Django has already logged the traceback (logger
    `django.request`) by the time this runs; this adds one line carrying a
    short reference, printed next to it in the journal, and shows the same
    reference on screen. "It says K7Q2MX" finds the right traceback in
    seconds, where "it broke this morning" does not.

    Nothing here may touch the database or the session: either could be
    what failed. If rendering the page fails too, a bare response is still
    better than none.
    """
    import logging

    from django.http import HttpResponseServerError
    from django.template.loader import render_to_string

    reference = services.temporary_password()[:6].upper()
    try:
        user_id = getattr(request, "user", None) and request.user.pk
    except Exception:
        user_id = None
    logging.getLogger("a1360.errors").error(
        "Server error ref=%s %s %s user=%s",
        reference, request.method, request.get_full_path(), user_id or "-",
    )
    try:
        return HttpResponseServerError(render_to_string("500.html", {"error_ref": reference}))
    except Exception:
        return HttpResponseServerError(
            "Something went wrong on the server. Reference: {}".format(reference),
            content_type="text/plain",
        )
