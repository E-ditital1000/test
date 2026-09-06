"""
Root URLconf for the A1 Management System.

Every module mounts its own urls.py. Access is not decided here: each view
carries its own permission gate from the frozen registry, so a URL that is
reachable is still refused server-side unless the user holds the code.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),

    # The service worker must be served from the site root: a worker's scope
    # is its own directory, and one under /static/ could not control the app.
    path(
        "sw.js",
        TemplateView.as_view(
            template_name="sw.js", content_type="application/javascript"
        ),
        name="service-worker",
    ),
    path(
        "manifest.webmanifest",
        TemplateView.as_view(
            template_name="manifest.webmanifest",
            content_type="application/manifest+json",
        ),
        name="manifest",
    ),
    # Cached by the worker and shown when a screen has never been opened on
    # this device. Deliberately unauthenticated — it must render with no
    # network and no session.
    path(
        "offline/",
        TemplateView.as_view(template_name="shell/offline.html"),
        name="offline",
    ),
    path("", include("accounts.urls")),
    path("", include("config.urls")),
    path("", include("dashboard.urls")),
    path("", include("crm.urls")),
    path("", include("projects.urls")),
    path("", include("fieldjobs.urls")),
    path("", include("approvals.urls")),
    path("", include("finance.urls")),
    path("", include("hr.urls")),
    path("", include("reports.urls")),
]

# The refusal is a designed screen, not a stock error page: it names the
# permission that is missing and who can grant it.
handler403 = "accounts.views.permission_denied"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
