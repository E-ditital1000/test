"""
Django settings for the A-1 Management System (Phase One).
"""
import os
import sys
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, True),
)
environ.Env.read_env(BASE_DIR / ".env")

# Whether this process is running the test suite.
#
# It exists for one reason: a developer with real media credentials in their
# .env — which is the normal way to check uploads work — would otherwise have
# every test run push its fixture photographs into the live media account.
# The suite submits assessments with photographs attached, so that is not
# hypothetical, and the images would be indistinguishable from real site
# evidence once they were there.
TESTING = "test" in sys.argv or "pytest" in sys.modules

DEV_SECRET_KEY = "dev-only-insecure-secret-key-change-me"
SECRET_KEY = env("SECRET_KEY", default=DEV_SECRET_KEY)

# Railway sets these on every service it runs, so their presence is how this
# process knows it is not on somebody's laptop. Nothing has to be configured
# for the detection itself to work, which is the point: the settings that are
# dangerous to get wrong should not depend on remembering to set them.
ON_RAILWAY = bool(
    env("RAILWAY_ENVIRONMENT_NAME", default="") or env("RAILWAY_PROJECT_ID", default="")
)
RAILWAY_DOMAIN = env("RAILWAY_PUBLIC_DOMAIN", default="test-production-8c6f.up.railway.app")

# On by default on a laptop, off by default on the platform. A deployment
# that forgot this used to serve real tracebacks to the internet.
DEBUG = env.bool("DEBUG", default=not ON_RAILWAY)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
# The domain the platform gave this service. Without it a deployment that
# has not set ALLOWED_HOSTS answers every request with 400, and the only
# explanation is in a log the person looking at the blank page cannot see.
if RAILWAY_DOMAIN and RAILWAY_DOMAIN not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RAILWAY_DOMAIN)
# RAILWAY_PUBLIC_DOMAIN is not in the environment unless somebody thought to
# reference it, so the line above cannot be relied on. The generated domain
# is always under this suffix, and Railway controls who gets one, so trusting
# it costs nothing a custom domain would not already have to be listed for.
if ON_RAILWAY and ".up.railway.app" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(".up.railway.app")

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Must stay AFTER django.contrib.staticfiles: it ships its own
    # collectstatic whose copy_file is a no-op unless static files are served
    # from Cloudinary, and management commands resolve to the first app in
    # this list that defines them. Ahead of staticfiles it silently collects
    # nothing, which is a broken deploy that looks like a successful one.
    "cloudinary_storage",
    "rest_framework",
    "django_htmx",
    "crispy_forms",
    "crispy_bootstrap5",
    "cloudinary",
    "accounts.apps.AccountsConfig",
    "config.apps.ConfigConfig",
    "approvals.apps.ApprovalsConfig",
    "crm.apps.CrmConfig",
    "projects.apps.ProjectsConfig",
    "fieldjobs.apps.FieldjobsConfig",
    "finance.apps.FinanceConfig",
    "hr.apps.HrConfig",
    "reports.apps.ReportsConfig",
    "dashboard.apps.DashboardConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "accounts.middleware.ForcePasswordResetMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "inventoryproject.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.nav",
            ],
        },
    },
]

WSGI_APPLICATION = "inventoryproject.wsgi.application"

def _database_url():
    """
    Where the data lives, however the platform chose to say it.

    A managed Postgres is offered two ways: one DATABASE_URL, or the parts
    as PGHOST/PGUSER/PGPASSWORD/PGPORT and a database name. Reading only the
    first means a service wired up the second way falls through to the
    SQLite default and runs, wrongly, on the container's own disk.
    """
    url = env("DATABASE_URL", default="")
    if url:
        return url

    host = env("PGHOST", default="")
    if not host:
        return ""

    from urllib.parse import quote

    user = env("PGUSER", default="postgres")
    password = quote(env("PGPASSWORD", default=""), safe="")
    port = env("PGPORT", default="5432")
    name = env("PGDATABASE", default="") or env("POSTGRES_DB", default="railway")
    return f"postgres://{quote(user, safe='')}:{password}@{host}:{port}/{name}"


DATABASES = {
    "default": env.db_url_config(
        _database_url() or f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    )
}

# Two settings whose defaults are right for a laptop and dangerous on a
# server, and which fail silently rather than loudly when they are missed.
#
# DATABASE_URL: unset in a container, the fallback above is a SQLite file on
# the container's own disk. The app runs perfectly well against it and says
# nothing, while starting empty on every deploy and taking the day's clock
# events with it when the container is replaced.
#
# SECRET_KEY: unset, the fallback is a published string. It signs sessions
# and password reset links, so anyone who has read this repository can mint
# both.
#
# Neither is a warning. A deployment that has missed either should refuse to
# start, while there is still nothing depending on it.
# collectstatic runs during the image build, touches no database, and on
# some platforms runs before the service's variables are attached at all.
# Refusing it over a missing DATABASE_URL would fail the build for a reason
# that has nothing to do with what it is doing, and there would be no
# deployment left to correct.
BUILD_STEP = "collectstatic" in sys.argv

if not DEBUG and not TESTING and not BUILD_STEP:
    from django.core.exceptions import ImproperlyConfigured

    if not _database_url():
        # What this process can actually see, by name. A deployment that
        # believes it set the variable and a deployment that did not look
        # identical from here otherwise, and the difference is usually a
        # reference to a variable the other service never had. Names only —
        # a password must not end up in a deploy log.
        watched = [
            "DATABASE_URL", "PGHOST", "PGUSER", "PGPASSWORD", "PGPORT",
            "PGDATABASE", "POSTGRES_DB", "RAILWAY_ENVIRONMENT_NAME",
        ]
        seen = []
        for name in watched:
            if name not in os.environ:
                continue
            seen.append(name if os.environ[name].strip() else f"{name} (empty)")

        raise ImproperlyConfigured(
            "No database is configured and DEBUG is off. Refusing to start on "
            "the SQLite fallback, which lives on the container's own disk and "
            "is destroyed on the next deploy.\n"
            "  Database variables this process can see: "
            + (", ".join(seen) if seen else "none of them")
            + "\n"
            "  Set DATABASE_URL on THIS service. A ${{Service.VAR}} reference "
            "resolves to nothing when the named service has no such variable, "
            "and an empty value looks exactly like an unset one from here.\n"
            "  See docs/deploy-railway.md."
        )
    if SECRET_KEY == DEV_SECRET_KEY:
        raise ImproperlyConfigured(
            "SECRET_KEY is still the development default, which is published "
            "in this repository, and DEBUG is off. Generate one with: "
            "python -c \"from django.core.management.utils import "
            "get_random_secret_key as k; print(k())\""
        )

# SQLite serialises writers and, by default, blocks readers behind them and
# gives up immediately. That is fine for one person on `runserver` and not
# fine the moment anything is concurrent — the browser tests read from the
# test thread while the live-server thread is writing a clock event, and hit
# "database table is locked".
#
# Write-ahead logging lets a reader carry on during a write, and the timeout
# makes a blocked writer wait rather than fail. Production is PostgreSQL, so
# this only ever applies to development and the test suite.
if "sqlite" in DATABASES["default"]["ENGINE"]:
    DATABASES["default"].setdefault("OPTIONS", {}).update(
        {"timeout": 20, "init_command": "PRAGMA journal_mode=WAL;"}
    )

AUTH_USER_MODEL = "accounts.User"

# Phase One auth scope: people sign in with email and password. The stock
# ModelBackend stays registered behind it so `createsuperuser` and the
# Django admin continue to work by username.
AUTHENTICATION_BACKENDS = [
    "accounts.backends.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Django 5.1 removed STATICFILES_STORAGE and DEFAULT_FILE_STORAGE; STORAGES is
# the setting that actually applies. Cloudinary swaps the default backend below
# once credentials are present.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Hashed, compressed, manifest-backed static files are a deployment concern.
# Switching them on in development would make `runserver` and the test suite
# depend on someone having run collectstatic first — a missing manifest entry
# raises rather than falling back, so a fresh clone would fail to render.
if not DEBUG:
    STORAGES["staticfiles"]["BACKEND"] = (
        "whitenoise.storage.CompressedManifestStaticFilesStorage"
    )

# WhiteNoise reads every static file once at start-up and serves it from
# memory, which is right in production and wrong while building: a CSS edit
# is invisible until the server restarts, and the page silently renders
# against the previous stylesheet. Re-stat files in development only.
WHITENOISE_AUTOREFRESH = DEBUG

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Cloudinary is used for user-uploaded media (photos, receipts) once
# credentials are supplied via the environment; falls back to local
# filesystem storage for day-to-day local development.
CLOUDINARY_CLOUD_NAME = env("CLOUDINARY_CLOUD_NAME", default="")
# Never under test, whatever is configured: see TESTING at the top.
if CLOUDINARY_CLOUD_NAME and not TESTING:
    CLOUDINARY_STORAGE = {
        "CLOUD_NAME": CLOUDINARY_CLOUD_NAME,
        "API_KEY": env("CLOUDINARY_API_KEY", default=""),
        "API_SECRET": env("CLOUDINARY_API_SECRET", default=""),
    }
    STORAGES["default"]["BACKEND"] = "cloudinary_storage.storage.MediaCloudinaryStorage"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard-index"
LOGOUT_REDIRECT_URL = "login"

# A site assessment arrives as ONE JSON body carrying its photographs as
# base64 data URLs — that is what lets a technician fill the whole thing in
# with no signal and push it in a single idempotent request. Twelve 1280 px
# frames plus base64's 33% overhead runs to roughly 4 MB, well past Django's
# 2.5 MB default, which would reject an assessment somebody spent an
# afternoon on. nginx needs a matching client_max_body_size; see
# deploy/nginx.conf.
DATA_UPLOAD_MAX_MEMORY_SIZE = env.int("DATA_UPLOAD_MAX_MEMORY_SIZE", default=25 * 1024 * 1024)

# --------------------------------------------------------------------------
# Transport security
#
# Applied only when DEBUG is off, so `runserver` on http://127.0.0.1 keeps
# working. On the VPS these are what make the session cookie safe to carry a
# technician's login across a mobile network.
# --------------------------------------------------------------------------
if not DEBUG:
    # nginx terminates TLS and forwards over http, so Django has to be told
    # how to recognise an already-secure request. Without this,
    # SECURE_SSL_REDIRECT sends the browser into an endless redirect loop.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

    # Django's CSRF check needs the site's real origin behind a proxy.
    # e.g. CSRF_TRUSTED_ORIGINS=https://a1360.example.com
    CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
    # The platform's own domain, so signing in works on a deployment that has
    # set nothing. Without it every POST is rejected and the sign-in page just
    # reloads, which looks like a wrong password rather than a missing setting.
    if RAILWAY_DOMAIN:
        origin = f"https://{RAILWAY_DOMAIN}"
        if origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(origin)
    # And the same safety net as ALLOWED_HOSTS, for the same reason: without
    # it the sign-in form is rejected as a CSRF failure, which reads to the
    # person typing as a wrong password.
    if ON_RAILWAY and "https://*.up.railway.app" not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append("https://*.up.railway.app")

    # HSTS is deliberately OFF by default. It tells browsers to refuse plain
    # HTTP for this domain for the whole period, and that instruction cannot
    # be recalled from a browser that has already cached it. Turn it on
    # (SECURE_HSTS_SECONDS=31536000) only once HTTPS is confirmed working on
    # the real domain — not before.
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
    if SECURE_HSTS_SECONDS:
        SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
            "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
        )
        SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}


# Auth hardening (Phase One scope: email+password, forced reset on first
# login, lockout after repeated failures — enforced in accounts.views).
LOGIN_LOCKOUT_THRESHOLD = 5
LOGIN_LOCKOUT_MINUTES = 15
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 hours

# --------------------------------------------------------------------------
# Logging
#
# Everything goes to stderr. Under systemd that is the journal
# (`journalctl -u a1360`), which already timestamps, rotates and keeps it;
# a log file of our own would be one more thing to rotate and fill a disk.
#
# Without this, Django's default sends a production traceback only to
# ADMINS by email, and with no ADMINS and no mail server set up, a crash in
# the pilot would leave no trace anywhere.
#
# django is held at ERROR: its 403/404 warnings are already in the nginx
# access log, and repeating them here buries the errors that matter.
# --------------------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "stderr": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "loggers": {
        "django": {"handlers": ["stderr"], "level": "ERROR", "propagate": False},
        "a1360": {"handlers": ["stderr"], "level": LOG_LEVEL, "propagate": False},
    },
}

JAZZMIN_SETTINGS = {
    "site_header": "A1 Management System",
    "site_brand": "A1 Admin",
    "site_title": "A1 Admin",
    "navigation_expanded": True,
    "site_copyright": "A-1 E-Digital Network",
    "show_powered_by": False,
    "theme": "flatly",
}
