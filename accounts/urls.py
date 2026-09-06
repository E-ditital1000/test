from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/reset-required/", views.password_reset_required, name="password-reset-required"),

    path("settings/users/", views.settings_users, name="settings-users"),
    path("settings/users/new/", views.user_edit, name="settings-user-create"),
    path("settings/users/<int:pk>/", views.user_edit, name="settings-user-edit"),
    path("settings/users/<int:pk>/deactivate/", views.user_deactivate, name="settings-user-deactivate"),

    path("settings/roles/", views.settings_roles, name="settings-roles"),
    path("settings/roles/new/", views.role_edit, name="settings-role-create"),
    path("settings/roles/<int:pk>/", views.role_edit, name="settings-role-edit"),

    path("settings/audit-log/", views.audit_log, name="settings-audit-log"),
]
