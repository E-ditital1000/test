from django.urls import path

from . import profile_views, views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/reset-required/", views.password_reset_required, name="password-reset-required"),

    # No pk anywhere under /profile/: each screen acts on whoever is signed in.
    path("profile/", profile_views.profile_details, name="profile"),
    path("profile/photo/", profile_views.profile_photo, name="profile-photo"),
    path("profile/photo/remove/", profile_views.profile_photo_remove, name="profile-photo-remove"),
    path("profile/security/", profile_views.profile_security, name="profile-security"),
    path("profile/activity/", profile_views.profile_activity, name="profile-activity"),

    path("settings/users/", views.settings_users, name="settings-users"),
    path("settings/users/new/", views.user_edit, name="settings-user-create"),
    path("settings/users/<int:pk>/", views.user_edit, name="settings-user-edit"),
    path("settings/users/<int:pk>/deactivate/", views.user_deactivate, name="settings-user-deactivate"),

    path("settings/roles/", views.settings_roles, name="settings-roles"),
    path("settings/roles/new/", views.role_edit, name="settings-role-create"),
    path("settings/roles/<int:pk>/", views.role_edit, name="settings-role-edit"),

    path("settings/audit-log/", views.audit_log, name="settings-audit-log"),
]
