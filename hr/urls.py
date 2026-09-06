from django.urls import path

from . import views

urlpatterns = [
    path("hr/employees/", views.employees, name="hr-employees"),
    path("hr/employees/new/", views.employee_edit, name="hr-employee-create"),
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
]
