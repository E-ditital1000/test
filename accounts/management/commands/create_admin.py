"""
The first account on a fresh database: an Admin, so somebody can sign in.

A1 360 has no sign-up screen by design. Staff are taken on through the import,
and the import needs an actor who already holds `manage_employees` — so on a
brand-new database nobody can create the first account, because creating one
needs an account. This command is the way in.

It reads the account's details from the environment, never the repository, so
the password is not committed and not in the image:

    ADMIN_EMAIL     the address this person signs in with
    ADMIN_PASSWORD  a TEMPORARY password — the account is forced to change it
                    at first sign-in, so it stops working the moment they do
    ADMIN_NAME      optional full name (default "Daniel Padmore")

It runs on every deploy from start.sh and is idempotent in the way that
matters: it creates the account once, and once the account exists it is left
alone — the password is never rewritten, so a deploy cannot undo the reset the
person did at first sign-in. With the two variables unset it does nothing and
says so, rather than failing the deploy.

Full access is granted two ways, and this does both. `is_superuser` bypasses
the permission model outright (see accounts.permissions.permission_map), which
is what guarantees the very first account can reach everything before any role
exists. The Admin role is attached as well, so the app's own Settings screen
shows the account for what it is and an Admin can manage it there like any
other — A-1 has no CEO role, and Admin is what stands in for one.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Role, UserRole

User = get_user_model()

DEFAULT_NAME = "Daniel Padmore"


def split_name(full):
    """First / middle / last, the way User.get_full_name expects them.

    Three names is the common case here, and the middle one is not decoration
    — it is how two people who share a first and last name are told apart.
    """
    parts = full.split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


class Command(BaseCommand):
    help = "Create the first Admin account from ADMIN_EMAIL / ADMIN_PASSWORD, once."

    @transaction.atomic
    def handle(self, *args, **options):
        email = (os.environ.get("ADMIN_EMAIL") or "").strip()
        password = os.environ.get("ADMIN_PASSWORD") or ""
        name = (os.environ.get("ADMIN_NAME") or DEFAULT_NAME).strip()

        if not email or not password:
            self.stdout.write(
                "    ADMIN_EMAIL / ADMIN_PASSWORD not set — no admin account "
                "created. Set both on this service to create one."
            )
            return

        existing = User.objects.filter(email__iexact=email).first()
        if existing is not None:
            # The account is already here. Leave its password and its name
            # exactly as they are — both may have changed since it was made —
            # and only make sure its access is still in place.
            changed = []
            if not existing.is_superuser:
                existing.is_superuser = True
                changed.append("is_superuser")
            if not existing.is_staff:
                existing.is_staff = True
                changed.append("is_staff")
            if changed:
                existing.save(update_fields=changed)
            self._grant_admin_role(existing)
            self.stdout.write(
                f"    admin account {email} already exists — left as it is."
            )
            return

        first, middle, last = split_name(name)
        user = User(
            username=email,
            email=email,
            first_name=first,
            middle_name=middle,
            last_name=last,
            is_staff=True,
            is_superuser=True,
            # Forced to choose their own at first sign-in, which is what makes
            # it safe for the temporary one to have been set from a variable.
            must_reset_password=True,
        )
        user.set_password(password)
        user.save()
        self._grant_admin_role(user)
        self.stdout.write(
            self.style.SUCCESS(
                f"    created admin account {email} ({user.get_full_name()}). "
                "They must set a new password at first sign-in."
            )
        )

    def _grant_admin_role(self, user):
        role = Role.objects.filter(name="Admin").first()
        if role is None:
            # start.sh seeds permissions before this runs, so this is only
            # reachable if run by hand too early. Superuser access is already
            # in place regardless, so this is a note, not a failure.
            self.stdout.write(
                "    (no Admin role yet — run seed_permissions; superuser "
                "access is already in place.)"
            )
            return
        UserRole.objects.get_or_create(user=user, role=role)
