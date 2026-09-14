from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

SCOPE_ALL = "all"
SCOPE_OWN_TEAM = "own_team"
SCOPE_OWN_PROJECTS = "own_projects"

SCOPE_CHOICES = [
    (SCOPE_ALL, "All"),
    (SCOPE_OWN_TEAM, "Own team"),
    (SCOPE_OWN_PROJECTS, "Own projects"),
]


class User(AbstractUser):
    """
    Auth identity only. Employment/HR fields (staff_id, phone, supervisor,
    department, ...) live on hr.Employee, one-to-one with this model, so the
    permission/auth layer never depends on the HR module being present.
    """

    must_reset_password = models.BooleanField(default=True)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def is_locked_out(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    def __str__(self):
        return self.get_full_name() or self.username


class Permission(models.Model):
    """
    The fixed, seeded list of actions the system can gate. This table is the
    Wave 1 contract: `accounts.permission_registry` is authoritative and this
    table is generated from it by `seed_permissions` — never hand-edited.
    """

    code = models.SlugField(max_length=64, unique=True)
    module = models.CharField(max_length=30)
    description = models.CharField(max_length=200)

    class Meta:
        ordering = ["module", "code"]

    def __str__(self):
        return self.code


class Role(models.Model):
    """
    A named bundle of permissions. Pre-built roles (is_system=True) are
    starting points an Admin can copy or amend — never a fixed ceiling, and
    never referenced by name in permission-check code.
    """

    name = models.CharField(max_length=50, unique=True)
    is_system = models.BooleanField(default=False)
    permissions = models.ManyToManyField(Permission, through="RolePermission")

    def __str__(self):
        return self.name


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE)
    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default=SCOPE_ALL)

    class Meta:
        unique_together = ("role", "permission")

    def __str__(self):
        return f"{self.role} / {self.permission} ({self.scope})"


class UserRole(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="user_roles")

    class Meta:
        unique_together = ("user", "role")

    def __str__(self):
        return f"{self.user} -> {self.role}"


class AuditEntry(models.Model):
    """
    Append-only. Every module's approve/amend/role-change action writes here
    via accounts.audit.record_change — this table is never edited in place.
    """

    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="audit_entries")
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=64)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "audit entries"

    def __str__(self):
        return f"{self.action} on {self.target_type}:{self.target_id} by {self.actor}"


def profile_photo_path(instance, filename):
    """
    A fresh random name every upload. A predictable path (the user's id)
    would let anybody who has seen one photo guess the next person's, and a
    reused name would be served from browser caches after a replacement.
    """
    import uuid

    return f"profiles/{uuid.uuid4().hex}.jpg"


class Profile(models.Model):
    """
    What a person says about themselves, kept apart from both the sign-in
    identity (User) and the record HR keeps about them (hr.Employee).

    Only the person edits it. Nothing here grants access to anything, and
    nothing an employer relies on (staff ID, job title, supervisor) lives
    here, so a person changing their own profile can never change what
    they are allowed to do or how they are paid.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")

    photo = models.ImageField(upload_to=profile_photo_path, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    # On by default, because A-1 marks birthdays. Only the day and month are
    # ever shown to anyone else; the year, and so the age, never is.
    show_birthday = models.BooleanField(
        "Show my birthday to colleagues",
        default=True,
        help_text="Colleagues see the day and month on the dashboard. Never the year.",
    )
    bio = models.CharField("About me", max_length=280, blank=True)
    personal_phone = models.CharField(max_length=30, blank=True)
    address = models.CharField("Home address", max_length=200, blank=True)

    emergency_contact_name = models.CharField("Name", max_length=120, blank=True)
    emergency_contact_relationship = models.CharField("Relationship", max_length=60, blank=True)
    emergency_contact_phone = models.CharField("Phone", max_length=30, blank=True)

    linkedin_url = models.URLField("LinkedIn", blank=True)
    x_url = models.URLField("X (Twitter)", blank=True)
    facebook_url = models.URLField("Facebook", blank=True)
    website_url = models.URLField("Website", blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile of {self.user}"

    @property
    def social_links(self):
        """(label, url) for each link filled in, in display order."""
        pairs = [
            ("LinkedIn", self.linkedin_url),
            ("X", self.x_url),
            ("Facebook", self.facebook_url),
            ("Website", self.website_url),
        ]
        return [(label, url) for label, url in pairs if url]

    @property
    def has_emergency_contact(self):
        return bool(self.emergency_contact_name and self.emergency_contact_phone)
