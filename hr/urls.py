from django.urls import path

from . import code_views, views

urlpatterns = [
    path("hr/employees/", views.employees, name="hr-employees"),
    # Taking somebody on: person, sign-in and access in one act.
    path("hr/employees/new/", views.employee_onboard, name="hr-employee-create"),
    # Editing an existing record; the account already exists by then.
    path("hr/employees/<int:pk>/edit/", views.employee_edit, name="hr-employee-edit"),
    path("hr/employees/<int:pk>/deactivate/", views.employee_deactivate, name="hr-employee-deactivate"),

    # The employee's own surface.
    path("hr/clock/", views.clock, name="hr-clock"),
    path("hr/clock/event/", views.clock_event, name="hr-clock-event"),

    # Supervisor surfaces.
    path("hr/roll-call/", views.roll_call, name="hr-roll-call"),
    path("hr/attendance/<int:pk>/", views.employee_attendance, name="hr-employee-attendance"),
    path("hr/events/<int:pk>/correct/", views.correction_create, name="hr-correction-create"),
    path("hr/report/", views.monthly_report, name="hr-monthly-report"),

    # The codes HR prints and posts where crews clock.
    path("hr/codes/", code_views.codes, name="hr-codes"),
    path("hr/codes/new/", code_views.code_create, name="hr-code-create"),
    path("hr/codes/<int:pk>/print/", code_views.code_print, name="hr-code-print"),
    path("hr/codes/<int:pk>/qr.png", code_views.code_image, name="hr-code-image"),
    path("hr/codes/<int:pk>/revoke/", code_views.code_revoke, name="hr-code-revoke"),
]
