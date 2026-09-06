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
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string

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

def _demo_accounts():
    """
    One working account per role, read from the database rather than
    hardcoded, so the list can never drift from what actually exists.

    Guarded by SHOW_DEMO_ACCOUNTS, which defaults to DEBUG: this is a list of
    live credentials on an unauthenticated page.
    """
    if not django_settings.SHOW_DEMO_ACCOUNTS:
        return []

    from .navigation import visible_items

    rows = []
    for role in Role.objects.order_by("-is_system", "name"):
        account = (
            User.objects.filter(user_roles__role=role, is_active=True)
            # Prefer one that signs straight in over one still facing the
            # forced password reset.
            .order_by("must_reset_password", "email")
            .first()
        )
        if account is None:
            continue
        rows.append(
            {
                "role": role.name,
                "email": account.email,
                "modules": len(visible_items(account)),
                "must_reset": account.must_reset_password,
            }
        )
    return rows


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
                "It unlocks in {} minute(s), or an Admin can release it now.".format(
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
            "demo_accounts": _demo_accounts(),
            "demo_password": django_settings.DEMO_ACCOUNT_PASSWORD,
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
    return render(
        request,
        "accounts/settings_users.html",
        {
            "users": users,
            "query": query,
            "roles": Role.objects.all(),
            # "Nothing matches" must say how many records exist in total,
            # so an empty result never reads as an empty system.
            "total_users": User.objects.count(),
        },
    )


@require_permission("manage_users")
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
            temporary = get_random_string(12)
            user.set_password(temporary)
            user.must_reset_password = True
        user.save()

        _sync_roles(request, user, form.cleaned_data["roles"])
        audit.record_change(
            actor=request.user,
            action="user.created" if instance is None else "user.updated",
            target=user,
            before=before,
            after=audit.snapshot(user, fields=tracked),
            reason=request.POST.get("reason", ""),
        )
        if temporary:
            messages.success(
                request,
                "Account created. Temporary password: {} - the user must "
                "change it at first login.".format(temporary),
            )
        else:
            messages.success(request, "Account updated.")
        return redirect("settings-users")

    return render(request, "accounts/user_form.html", {"form": form, "instance": instance})


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
            "entries": entries[:300],
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
