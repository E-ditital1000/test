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
