"""
Django settings for the A-1 Management System (Phase One).
"""
import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, True),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-only-insecure-secret-key-change-me")

DEBUG = env.bool("DEBUG", default=True)

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

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

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}

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
if CLOUDINARY_CLOUD_NAME:
    CLOUDINARY_STORAGE = {
        "CLOUD_NAME": CLOUDINARY_CLOUD_NAME,
        "API_KEY": env("CLOUDINARY_API_KEY", default=""),
        "API_SECRET": env("CLOUDINARY_API_SECRET", default=""),
    }
    STORAGES["default"]["BACKEND"] = "cloudinary_storage.storage.MediaCloudinaryStorage"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard-index"
LOGOUT_REDIRECT_URL = "login"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# The sign-in screen can list the seeded demo accounts so roles can be
# compared quickly while building. This is a list of working credentials on a
# public page, so it defaults to DEBUG only and needs a deliberate, named
# opt-in anywhere else. Turning it on against real data would be a breach.
SHOW_DEMO_ACCOUNTS = env.bool("SHOW_DEMO_ACCOUNTS", default=DEBUG)
DEMO_ACCOUNT_PASSWORD = env("DEMO_ACCOUNT_PASSWORD", default="Demo!Pass123")

# Auth hardening (Phase One scope: email+password, forced reset on first
# login, lockout after repeated failures — enforced in accounts.views).
LOGIN_LOCKOUT_THRESHOLD = 5
LOGIN_LOCKOUT_MINUTES = 15
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 hours

JAZZMIN_SETTINGS = {
    "site_header": "A1 Management System",
    "site_brand": "A1 Admin",
    "site_title": "A1 Admin",
    "navigation_expanded": True,
    "site_copyright": "A-1 E-Digital Network",
    "show_powered_by": False,
    "theme": "flatly",
}
