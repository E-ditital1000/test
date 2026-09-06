from django.urls import path

from . import views

urlpatterns = [
    path("settings/service-types/", views.service_types, name="settings-service-types"),
    path("settings/service-types/new/", views.service_type_edit, name="settings-service-type-create"),
    path("settings/service-types/<int:pk>/edit/", views.service_type_edit, name="settings-service-type-edit"),
    path("settings/service-types/<int:pk>/questions/", views.service_type_questions, name="settings-service-type-questions"),
    path("settings/questions/<int:pk>/retire/", views.question_retire, name="settings-question-retire"),
    path("settings/questions/<int:pk>/reorder/", views.question_reorder, name="settings-question-reorder"),

    path("settings/status-lists/", views.status_lists, name="settings-status-lists"),
    path("settings/status-lists/new/", views.status_edit, name="settings-status-create"),
    path("settings/status-lists/<int:pk>/", views.status_edit, name="settings-status-edit"),

    path("settings/company/", views.company_details, name="settings-company"),
    path("settings/policy/", views.policy_settings, name="settings-policy"),
]
