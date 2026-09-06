from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        # Registers the system checks that guard the frozen permission
        # contract, so an unknown code in a role grant or in the navigation
        # fails `manage.py check` rather than silently opening a hole.
        from . import checks  # noqa: F401
