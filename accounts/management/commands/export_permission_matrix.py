"""
Emits the permission list and the role-by-permission matrix as Markdown.

This is the Wave 1 contract document every other agent codes against, so it
is generated from the registry rather than maintained by hand — the
document cannot drift from what the system actually enforces. Regenerate it
whenever the registry or the pre-built roles change.
"""
from pathlib import Path

from django.core.management.base import BaseCommand

from accounts.permission_registry import PERMISSIONS
from accounts.seed_data import ROLE_GRANTS

SCOPE_MARK = {"all": "✅", "own_team": "👥", "own_projects": "📁"}


class Command(BaseCommand):
    help = "Generate the permission list and role-by-permission matrix document."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="docs/permission-matrix.md")

    def handle(self, *args, **options):
        roles = list(ROLE_GRANTS)
        grants = {
            role: {code: scope for code, scope in ROLE_GRANTS[role]} for role in roles
        }

        lines = [
            "# Permission list and role matrix",
            "",
            "**Generated — do not edit by hand.** Run "
            "`python manage.py export_permission_matrix` after changing "
            "`accounts/permission_registry.py` or `accounts/seed_data.py`.",
            "",
            "A role is a named bundle of permissions. Every screen and every "
            "endpoint asks *does this user hold this permission*, never *is "
            "this user a supervisor*. The roles below are the pre-built "
            "starting points an Admin can copy or amend in Settings — they "
            "are not a fixed set, and a custom role created at runtime is "
            "enforced exactly like these.",
            "",
            "Scope: ✅ all · 👥 own team · 📁 own projects · blank = not granted.",
            "",
            "| Permission | Module | " + " | ".join(roles) + " |",
            "| --- | --- | " + " | ".join(["---"] * len(roles)) + " |",
        ]

        for code, module, description in PERMISSIONS:
            cells = [SCOPE_MARK.get(grants[role].get(code), "") for role in roles]
            lines.append(f"| `{code}` | {module} | " + " | ".join(cells) + " |")

        lines += ["", "## What each permission allows", ""]
        current_module = None
        for code, module, description in PERMISSIONS:
            if module != current_module:
                lines += [f"### {module}", ""]
                current_module = module
            lines.append(f"- `{code}` — {description}")
        lines.append("")

        path = Path(options["output"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {path} — {len(PERMISSIONS)} permissions across {len(roles)} roles."
            )
        )
