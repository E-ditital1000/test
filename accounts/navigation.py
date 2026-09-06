"""
Navigation is generated from the user's permissions, never from their role
name. A module a user cannot use is ABSENT from their shell — not greyed
out. Because the same permission code gates the route server-side, hiding
it is a convenience for the user, never the control.

Two surfaces, one rule:

  * the desktop sidebar — the eight modules of the design specification
  * the mobile bottom tab bar — at most four tabs, so an Employee who only
    clocks in gets two, not four with two disabled

A module is shown when the user holds ANY of its targets, and it links to
the first target they actually hold. That matters for a Supervisor who can
review assessments but has no job list of their own: they get Field Jobs,
pointed at the review queue rather than at a screen that would refuse them.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    label: str
    icon: str
    # ((permission_code, url_name), ...) — first one held wins.
    targets: tuple

    @property
    def permissions(self):
        return [code for code, _url in self.targets]


@dataclass(frozen=True)
class ResolvedNavItem:
    label: str
    icon: str
    url_name: str


# The eight modules, in the order the specification shows them.
NAV_ITEMS = [
    NavItem("Dashboard", "dashboard", (
        ("view_dashboard", "dashboard-index"),
    )),
    NavItem("Customers & Tickets", "tickets", (
        # Tickets first: it is the operational screen people live in. Someone
        # who can only see the register lands there instead.
        ("view_ticket_status", "crm-tickets"),
        ("view_customers", "crm-customers"),
    )),
    NavItem("Projects", "projects", (
        ("view_projects", "projects-list"),
    )),
    NavItem("Field Jobs", "field", (
        ("view_own_job_list", "fieldjobs-my-jobs"),
        ("review_assessment", "approvals-queue"),
    )),
    NavItem("Finance", "finance", (
        ("view_invoices", "finance-invoices"),
        ("log_expense", "finance-invoices"),
        ("approve_requisition", "finance-invoices"),
    )),
    NavItem("Reports", "reports", (
        ("view_reports", "reports-index"),
    )),
    NavItem("HR", "hr", (
        ("view_employees", "hr-employees"),
        ("view_attendance_records", "hr-employees"),
        ("clock_in_out", "hr-clock"),
    )),
    NavItem("Settings", "settings", (
        ("manage_users", "settings-users"),
        ("manage_roles", "settings-roles"),
        ("manage_service_types", "settings-service-types"),
        ("manage_status_lists", "settings-status-lists"),
        ("manage_company_details", "settings-company"),
        ("view_audit_log", "settings-audit-log"),
    )),
]

# The phone. Capped at four by the specification: a fifth tab would push
# every target below the comfortable one-handed reach.
MOBILE_TABS = [
    NavItem("Jobs", "field", (("view_own_job_list", "fieldjobs-my-jobs"),)),
    NavItem("Sync", "sync", (("view_own_job_list", "fieldjobs-my-jobs"),)),
    NavItem("Clock", "clock", (("clock_in_out", "hr-clock"),)),
    NavItem("Me", "me", (("clock_in_out", "hr-clock"),)),
]

MAX_TABS = 4

# Which module a URL belongs to, by url-name prefix. A detail screen, a form
# and a queue all light up their own module in the sidebar, so the user is
# never looking at a page whose module appears unselected. Longest prefix
# wins, so "approvals-" resolves before any shorter overlap.
URL_PREFIX_MODULE = {
    "dashboard-": "dashboard",
    "crm-": "tickets",
    "projects-": "projects",
    "fieldjobs-": "field",
    "approvals-": "field",
    "finance-": "finance",
    "reports-": "reports",
    "hr-": "hr",
    "settings-": "settings",
}


# Which mobile tab a URL lights up. Separate from the sidebar map because
# the tabs are not the modules: Jobs and Sync are two tabs over one module,
# and Clock and Me are two over another.
URL_TAB = {
    "hr-clock": "clock",
    "fieldjobs-my-jobs": "field",
    "approvals-queue": "field",
}


def active_module(url_name):
    """The icon key of the module a given url name belongs to, or ''."""
    if not url_name:
        return ""
    for prefix in sorted(URL_PREFIX_MODULE, key=len, reverse=True):
        if url_name.startswith(prefix):
            return URL_PREFIX_MODULE[prefix]
    return ""


def active_tab(url_name):
    """
    The icon key of the mobile tab a given url name lights up. A view may
    override this in its own context — a Sync screen sharing the job list's
    URL cannot be told apart here.
    """
    return URL_TAB.get(url_name or "", "")


def _resolve(items, user):
    from .decorators import user_has_permission

    if not user.is_authenticated:
        return []
    resolved = []
    for item in items:
        for code, url_name in item.targets:
            if user_has_permission(user, code):
                resolved.append(ResolvedNavItem(item.label, item.icon, url_name))
                break
    return resolved


def visible_items(user):
    """The desktop sidebar for this user."""
    return _resolve(NAV_ITEMS, user)


def visible_tabs(user):
    """The mobile tab bar for this user, capped at four."""
    return _resolve(MOBILE_TABS, user)[:MAX_TABS]
