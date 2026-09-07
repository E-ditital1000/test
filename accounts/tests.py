"""
Wave 1 access-control acceptance tests.

The two the brief calls for by name are here: every pre-built role attempts
every gated endpoint and is correctly allowed or refused, and a custom role
created at runtime reaches exactly the endpoints it was granted and no
others. The guard rails from section 5 are tested alongside them.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.conf import settings
from django.test import SimpleTestCase, TestCase, tag
from django.urls import reverse
from django.utils import timezone

from . import services
from .decorators import user_has_permission
from .models import Role, UserRole
from .navigation import visible_items
from .permission_registry import PERMISSION_CODES
from .seed_data import ROLE_GRANTS

User = get_user_model()

# Every permission-gated GET endpoint in Wave 1, with the code that gates
# it. Adding a screen without adding it here is caught by
# `test_every_gated_endpoint_is_covered`.
ENDPOINTS = {
    "dashboard-index": "view_dashboard",
    "dashboard-command-view": "view_executive_dashboard",
    "crm-customers": "view_customers",
    "projects-list": "view_projects",
    "fieldjobs-my-jobs": "view_own_job_list",
    "approvals-queue": "review_assessment",
    "finance-invoices": "view_invoices",
    "reports-index": "view_reports",
    "hr-employees": "view_employees",
    "hr-employee-create": "manage_employees",
    "hr-clock": "clock_in_out",
    "settings-users": "manage_users",
    "settings-user-create": "manage_users",
    "settings-roles": "manage_roles",
    "settings-role-create": "manage_roles",
    "settings-service-types": "manage_service_types",
    "settings-status-lists": "manage_status_lists",
    "settings-company": "manage_company_details",
    "settings-policy": "manage_company_details",
    "settings-service-type-create": "manage_service_types",
    "settings-status-create": "manage_status_lists",
    "settings-audit-log": "view_audit_log",
    # Wave 2 — Customers & Tickets
    "crm-tickets": "view_ticket_status",
    "crm-ticket-create": "create_ticket",
    "crm-customer-create": "create_customer",
    # Wave 2 — Field Jobs, HR attendance and Finance
    "fieldjobs-schedule": "schedule_field_job",
    "hr-roll-call": "view_attendance_records",
    "hr-monthly-report": "view_attendance_reports",
    "finance-expenses": "log_expense",
    "finance-expense-create": "log_expense",
    "finance-invoice-create": "issue_invoice",
    "finance-requisitions": "approve_requisition",
    # Wave 3 / shell
    "global-search": "view_dashboard",
    "dashboard-my-tasks": "view_dashboard",
}

# Endpoints the matrix cannot GET meaningfully: they only accept POST, so a
# GET redirects rather than answering 200. They are still permission-gated
# and still discovered by the URLconf walk — they are excluded from the
# GET matrix only, and listed here deliberately rather than skipped silently.
POST_ONLY = {"hr-clock-event"}


def gated_endpoints_in_urlconf():
    """
    Every permission-gated view in the URLconf that takes no arguments,
    discovered by walking the resolver rather than being listed by hand.
    """
    from django.urls import get_resolver
    from django.urls.resolvers import URLPattern, URLResolver

    found = {}

    def walk(patterns):
        for entry in patterns:
            if isinstance(entry, URLResolver):
                walk(entry.url_patterns)
                continue
            if not isinstance(entry, URLPattern) or entry.name is None:
                continue
            code = getattr(entry.callback, "_a1_permission", None)
            # Views taking a pk cannot be reversed without a fixture, so the
            # matrix covers the argument-free ones.
            if code and not entry.pattern.regex.groupindex:
                found[entry.name] = code

    walk(get_resolver().url_patterns)
    return found


def make_user(email, role_names=(), **extra):
    user = User.objects.create_user(
        username=email, email=email, password="Testing!12345", **extra
    )
    # The forced reset is tested separately; these fixtures start past it.
    user.must_reset_password = False
    user.save(update_fields=["must_reset_password"])
    for name in role_names:
        UserRole.objects.create(user=user, role=Role.objects.get(name=name))
    return user


class SeededAccessControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)

    def test_every_gated_endpoint_is_covered(self):
        """The endpoint map only names codes from the frozen registry."""
        for url_name, code in ENDPOINTS.items():
            self.assertIn(code, PERMISSION_CODES, f"{url_name} gates on an unknown code")

    @tag("acceptance")
    def test_matrix_covers_every_gated_screen_in_the_urlconf(self):
        """
        A new gated screen must join the role matrix. Without this the matrix
        silently stops being a release gate as the system grows.
        """
        discovered = gated_endpoints_in_urlconf()
        missing = {
            n: c for n, c in discovered.items()
            if n not in ENDPOINTS and n not in POST_ONLY
        }
        self.assertEqual(
            missing,
            {},
            "These gated screens are not in the ENDPOINTS matrix: "
            + ", ".join(f"{n} ({c})" for n, c in sorted(missing.items())),
        )
        for name, code in discovered.items():
            if name in POST_ONLY:
                continue
            self.assertEqual(
                ENDPOINTS[name], code, f"{name} is listed against the wrong permission"
            )

    @tag("acceptance")
    def test_every_prebuilt_role_against_every_endpoint(self):
        """
        The matrix test: for each of the nine pre-built roles, every gated
        endpoint either answers or refuses, and which one is decided purely
        by whether the role's grants include that permission.
        """
        for role_name, grants in ROLE_GRANTS.items():
            held = {code for code, _scope in grants}
            user = make_user(f"{role_name.lower().replace(' ', '.')}@test.local", [role_name])
            self.client.force_login(user)

            for url_name, code in ENDPOINTS.items():
                with self.subTest(role=role_name, endpoint=url_name):
                    response = self.client.get(reverse(url_name))
                    if code in held:
                        self.assertEqual(
                            response.status_code,
                            200,
                            f"{role_name} holds {code} but was refused {url_name}",
                        )
                    else:
                        self.assertEqual(
                            response.status_code,
                            403,
                            f"{role_name} does not hold {code} but reached {url_name}",
                        )
            self.client.logout()

    def test_custom_role_created_at_runtime_reaches_exactly_its_grants(self):
        """
        An admin invents a Receptionist-style role in Settings, grants it
        two permissions, and the holder sees exactly those screens — with no
        code change and no release.
        """
        admin = make_user("admin@test.local", ["Admin"])
        role = services.create_role(
            actor=admin,
            name="Front Desk",
            grants=[("view_dashboard", "all"), ("view_customers", "all")],
        )

        clerk = make_user("clerk@test.local")
        services.assign_role(actor=admin, user=clerk, role=role)
        self.client.force_login(clerk)

        self.assertEqual(self.client.get(reverse("dashboard-index")).status_code, 200)
        self.assertEqual(self.client.get(reverse("crm-customers")).status_code, 200)
        for url_name in ("finance-invoices", "hr-employees", "settings-users", "reports-index"):
            with self.subTest(endpoint=url_name):
                self.assertEqual(self.client.get(reverse(url_name)).status_code, 403)

    def test_navigation_shows_only_usable_modules(self):
        """The client hides what a user cannot do; the server refuses it regardless."""
        technician = make_user("tech@test.local", ["Technician"])
        labels = {item.label for item in visible_items(technician)}
        self.assertIn("Field Jobs", labels)
        self.assertNotIn("Finance", labels)
        self.assertNotIn("Settings", labels)

        # Hiding the link is a convenience, not the control: the route still
        # refuses a technician who types the URL.
        self.client.force_login(technician)
        self.assertEqual(self.client.get(reverse("finance-invoices")).status_code, 403)

    def test_client_cannot_manipulate_its_way_into_a_permission(self):
        """A forged POST asking for a permission the user lacks is refused."""
        technician = make_user("tech2@test.local", ["Technician"])
        self.client.force_login(technician)
        response = self.client.post(
            reverse("settings-role-create"),
            {"name": "Self Promotion", "permissions": ["manage_roles"]},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Role.objects.filter(name="Self Promotion").exists())


class GuardRailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)

    def test_cannot_grant_a_permission_you_do_not_hold(self):
        # Someone trusted with role management, but holding nothing
        # financial themselves.
        supervisor = make_user("sup@test.local", ["Supervisor"])
        UserRole.objects.create(user=supervisor, role=self._role_with(["manage_roles"]))

        with self.assertRaises(PermissionDenied):
            services.create_role(
                actor=supervisor,
                name="Shadow Finance",
                grants=[("issue_invoice", "all")],
            )
        self.assertFalse(Role.objects.filter(name="Shadow Finance").exists())

    def test_cannot_grant_a_permission_that_does_not_exist(self):
        admin = make_user("admin3@test.local", ["Admin"])
        with self.assertRaises(ValidationError):
            services.create_role(
                actor=admin, name="Invented", grants=[("delete_the_company", "all")]
            )

    def test_last_admin_cannot_be_demoted_or_deactivated(self):
        admin = make_user("solo.admin@test.local", ["Admin"])
        admin_role = Role.objects.get(name="Admin")

        with self.assertRaises(ValidationError):
            services.revoke_role(actor=admin, user=admin, role=admin_role)
        with self.assertRaises(ValidationError):
            services.deactivate_user(actor=admin, user=admin)
        self.assertTrue(user_has_permission(admin, "manage_roles"))

        # With a second admin present, the first may be demoted.
        second = make_user("second.admin@test.local", ["Admin"])
        services.revoke_role(actor=second, user=admin, role=admin_role)
        self.assertFalse(user_has_permission(admin, "manage_roles"))

    def test_role_changes_are_audit_logged_with_actor_and_before_after(self):
        from .models import AuditEntry

        admin = make_user("admin4@test.local", ["Admin"])
        role = services.create_role(
            actor=admin, name="Store Keeper", grants=[("view_projects", "all")]
        )
        services.set_role_permissions(
            actor=admin,
            role=role,
            grants=[("view_projects", "all"), ("view_customers", "all")],
            reason="Needs the customer register",
        )

        entry = AuditEntry.objects.filter(action="role.permissions_changed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, admin)
        self.assertEqual(entry.reason, "Needs the customer register")
        self.assertIn("view_projects:all", entry.before["grants"])
        self.assertIn("view_customers:all", entry.after["grants"])

    def _role_with(self, codes):
        from .models import Permission, RolePermission

        role = Role.objects.create(name="Role Manager Only")
        for code in codes:
            RolePermission.objects.create(
                role=role, permission=Permission.objects.get(code=code), scope="all"
            )
        return role


class AuthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)

    def test_sign_in_with_email_and_password(self):
        make_user("person@test.local", ["Employee"])
        response = self.client.post(
            reverse("login"), {"email": "person@test.local", "password": "Testing!12345"}
        )
        self.assertRedirects(response, reverse("dashboard-index"))

    def test_forced_reset_blocks_every_other_screen_until_done(self):
        user = User.objects.create_user(
            username="new@test.local", email="new@test.local", password="Temp!12345678"
        )
        UserRole.objects.create(user=user, role=Role.objects.get(name="Employee"))
        self.assertTrue(user.must_reset_password)

        self.client.force_login(user)
        self.assertRedirects(
            self.client.get(reverse("dashboard-index")), reverse("password-reset-required")
        )

        self.client.post(
            reverse("password-reset-required"),
            {"new_password1": "Rotated!98765", "new_password2": "Rotated!98765"},
        )
        user.refresh_from_db()
        self.assertFalse(user.must_reset_password)
        self.assertEqual(self.client.get(reverse("dashboard-index")).status_code, 200)

    def test_account_locks_after_repeated_failures(self):
        user = make_user("target@test.local", ["Employee"])
        for _ in range(5):
            self.client.post(
                reverse("login"), {"email": "target@test.local", "password": "wrong-one"}
            )

        user.refresh_from_db()
        self.assertTrue(user.is_locked_out())

        # Even the correct password is refused while the lockout stands.
        response = self.client.post(
            reverse("login"), {"email": "target@test.local", "password": "Testing!12345"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_successful_sign_in_clears_the_failure_count(self):
        user = make_user("resets@test.local", ["Employee"])
        self.client.post(reverse("login"), {"email": "resets@test.local", "password": "nope"})
        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 1)

        self.client.post(
            reverse("login"), {"email": "resets@test.local", "password": "Testing!12345"}
        )
        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 0)
        self.assertIsNone(user.locked_until)

    def test_lockout_expires(self):
        user = make_user("expires@test.local", ["Employee"])
        user.locked_until = timezone.now() - timedelta(minutes=1)
        user.save(update_fields=["locked_until"])
        self.assertFalse(user.is_locked_out())


class TemplateHygieneTests(TestCase):
    """
    Guards against mistakes that render as visible text rather than failing.
    """

    def test_no_multiline_hash_comments_leak_into_output(self):
        """
        Django strips `{# ... #}` only when it sits on ONE line. Spanning
        lines makes it literal text the user reads on the page — which is
        exactly how a stylesheet note ended up on the sign-in screen. Use
        `{% comment %}` for anything longer than a line.
        """
        import pathlib

        offenders = []
        for template in sorted(pathlib.Path("templates").rglob("*.html")):
            text = template.read_text(encoding="utf-8")
            position = 0
            while True:
                start = text.find("{#", position)
                if start == -1:
                    break
                close = text.find("#}", start)
                line_end = text.find("\n", start)
                if close == -1 or (line_end != -1 and close > line_end):
                    offenders.append(f"{template}:{text[:start].count(chr(10)) + 1}")
                position = (close + 2) if close != -1 else len(text)

        self.assertEqual(
            offenders,
            [],
            "Multi-line {# #} comments render as visible text. "
            "Use {% comment %}...{% endcomment %} instead: " + ", ".join(offenders),
        )


class OfflineShellTests(TestCase):
    """
    The pieces that make a cold start work with no signal. Without these a
    technician who closes the app on site and reopens it gets the browser's
    error page — the queued work is safe but unreachable, which reads as lost.
    """

    @classmethod
    def setUpTestData(cls):
        call_command("seed_permissions", verbosity=0)

    def test_the_service_worker_is_served_from_the_site_root(self):
        """
        A worker's scope is its own directory. Served from /static/ it could
        not control the app, so this route must stay at the root.
        """
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")

    def test_the_service_worker_never_caches_a_write(self):
        """
        A clock event or an assessment is queued by the page and replayed
        deliberately. Replaying it from the worker would leave nothing able
        to report the outcome to the person who tapped.
        """
        source = self.client.get("/sw.js").content.decode()
        self.assertIn('request.method !== "GET"', source)
        # Uploaded files and exports are never held on the device.
        self.assertIn("/media/", source)
        self.assertIn("export", source)

    def test_the_offline_page_renders_without_a_session(self):
        """It is served from the cache, so it cannot need the network."""
        response = self.client.get(reverse("offline"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Nothing is lost", body)

    def test_the_manifest_is_served(self):
        response = self.client.get(reverse("manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")

    def test_cached_pages_are_cleared_when_nobody_is_signed_in(self):
        """
        On a shared phone this is what stops the next person seeing the last
        person's jobs.
        """
        anonymous = self.client.get(reverse("login")).content.decode()
        self.assertIn("clear-pages", anonymous)

        user = make_user("shell@test.local", ["Employee"])
        self.client.force_login(user)
        signed_in = self.client.get(reverse("hr-clock")).content.decode()
        self.assertNotIn("clear-pages", signed_in)

    def test_every_page_loads_the_shared_offline_layer(self):
        """
        Client-generated ids, GPS that never blocks and durable device
        storage are implemented once, not per screen.
        """
        user = make_user("shell2@test.local", ["Technician"])
        self.client.force_login(user)
        for url_name in ("fieldjobs-my-jobs", "hr-clock", "dashboard-index"):
            body = self.client.get(reverse(url_name)).content.decode()
            self.assertIn("a1-offline.js", body, url_name)


class OfflineHelperTests(TestCase):
    """
    The shared layer is JavaScript, so what is asserted here is that the
    contract it implements is present and that no screen has quietly gone
    back to its own copy.
    """

    def test_no_template_reimplements_the_offline_helpers(self):
        import pathlib

        offenders = []
        for template in sorted(pathlib.Path("templates").rglob("*.html")):
            text = template.read_text(encoding="utf-8")
            if "randomUUID" in text or "getCurrentPosition" in text:
                offenders.append(str(template))
        self.assertEqual(
            offenders,
            [],
            "These templates carry their own copy of a helper that belongs in "
            "static/js/a1-offline.js: " + ", ".join(offenders),
        )

    def test_photographs_are_not_stored_in_local_storage(self):
        """
        A site visit produces megabytes of images; localStorage throws part
        way through and loses the afternoon. Photos belong in IndexedDB,
        which is what A1.store uses.
        """
        import pathlib

        assessment = pathlib.Path("templates/fieldjobs/assessment.html").read_text(encoding="utf-8")
        # The word appears in a comment explaining why it is not used, so
        # assert on the call rather than the mention.
        for call in ("localStorage.setItem", "localStorage.getItem", "localStorage."):
            self.assertNotIn(call, assessment)
        self.assertIn("A1.store", assessment)

        layer = pathlib.Path("static/js/a1-offline.js").read_text(encoding="utf-8")
        self.assertIn("indexedDB", layer)


class NullOrderingTests(SimpleTestCase):
    """
    NULL placement in an ORDER BY is not portable: SQLite sorts NULLs first,
    PostgreSQL sorts them last. Development runs on SQLite and the server on
    Postgres, so any ordering that leaves it to the default means one thing
    here and the opposite there — and nobody connects the symptom to the
    database.

    Every ordering over a nullable column must say where NULLs go.
    """

    def test_no_model_orders_by_a_nullable_column_by_default(self):
        import django.apps
        from django.db.models.expressions import OrderBy

        ours = {
            "accounts", "config", "crm", "projects", "fieldjobs",
            "finance", "hr", "approvals", "dashboard", "reports",
        }
        offenders = []
        for model in django.apps.apps.get_models():
            if model._meta.app_label not in ours:
                continue
            nullable = {
                field.name for field in model._meta.get_fields()
                if getattr(field, "null", False)
            }
            for term in model._meta.ordering or []:
                # An OrderBy expression has already been explicit about it.
                if isinstance(term, OrderBy):
                    continue
                if isinstance(term, str) and term.lstrip("-") in nullable:
                    offenders.append(f"{model.__name__}.Meta.ordering: {term!r}")

        self.assertEqual(
            offenders,
            [],
            "these order by a nullable column without saying where NULLs go, so "
            "they sort differently on SQLite and PostgreSQL: " + ", ".join(offenders),
        )


class StylesheetCascadeTests(SimpleTestCase):
    """
    Two rules of equal specificity are settled by which one is written last,
    and that is not visible from any template, any view, or any other test in
    this suite.

    It bit for real: the field screens' desktop block sets
    `.fhero { display: grid }`, and it was written ABOVE `.mc { display: flex }`
    -- so the desktop hero silently stayed a flex column while every other
    rule in the same media query applied normally. Nothing failed. The page
    just quietly rendered the wrong layout.

    So: the block that overrides the field primitives must come after them.
    """

    CSS = settings.BASE_DIR / "static" / "css" / "a1.css"

    # Every primitive the desktop field block redeclares at equal or lower
    # specificity. Add to this when the block starts overriding something new.
    OVERRIDDEN = [
        ".phone {", ".phead {", ".pbody {", ".mc {", ".act {",
        ".a1-tabs {", ".a1-tab {", ".a1-badge {",
    ]
    MARKER = "/* Layout wrappers for the field screens."

    def setUp(self):
        self.css = self.CSS.read_text(encoding="utf-8")
        self.block_at = self.css.find(self.MARKER)
        self.assertNotEqual(
            self.block_at, -1,
            "the field layout block is gone or its opening comment changed; "
            "this test cannot check an ordering it cannot find",
        )

    def test_the_desktop_field_block_is_written_after_what_it_overrides(self):
        for selector in self.OVERRIDDEN:
            at = self.css.find(selector)
            self.assertNotEqual(at, -1, f"{selector} is no longer in the stylesheet")
            self.assertLess(
                at, self.block_at,
                f"{selector} is declared AFTER the field layout block, so it "
                f"wins on source order and the desktop override silently does "
                f"nothing. Move the block below it.",
            )

    def test_hidden_still_beats_every_layout_this_file_sets(self):
        """
        The wizard toggles steps with the `hidden` attribute while giving
        `.a-step` a display of its own. `[hidden]` only wins because it is
        !important -- this is the rule that keeps every step of an assessment
        from rendering at once.
        """
        self.assertIn(
            "[hidden] { display: none !important; }", self.css,
            "removing !important here renders every assessment step at once",
        )
