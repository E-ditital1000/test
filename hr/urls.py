from django.urls import path

from . import code_views, leave_views, views

urlpatterns = [
    path("hr/employees/", views.employees, name="hr-employees"),
    path("hr/departments/", views.departments, name="hr-departments"),
    path("hr/departments/new/", views.department_edit, name="hr-department-create"),
    path("hr/departments/<int:pk>/edit/", views.department_edit, name="hr-department-edit"),
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

    # Leave. The employee's own surface first, then HR's.
    path("hr/leave/", leave_views.my_leave, name="hr-leave-mine"),
    path("hr/leave/new/", leave_views.leave_request_create, name="hr-leave-create"),
    path("hr/leave/<int:pk>/withdraw/", leave_views.leave_cancel, name="hr-leave-cancel"),
    path("hr/leave/requests/", leave_views.leave_queue, name="hr-leave-queue"),
    path("hr/leave/requests/<int:pk>/", leave_views.leave_detail, name="hr-leave-detail"),
    path("hr/leave/requests/<int:pk>/decide/", leave_views.leave_decide, name="hr-leave-decide"),
    path("hr/leave/calendar/", leave_views.leave_calendar, name="hr-leave-calendar"),
    path("hr/leave/balances/", leave_views.leave_balances, name="hr-leave-balances"),
    path("hr/leave/adjustments/new/", leave_views.leave_adjustment_create, name="hr-leave-adjustment-create"),

    # The lists leave is computed from.
    path("hr/leave/settings/", leave_views.leave_settings, name="hr-leave-settings"),
    path("hr/leave/settings/types/new/", leave_views.leave_type_edit, name="hr-leave-type-create"),
    path("hr/leave/settings/types/<int:pk>/edit/", leave_views.leave_type_edit, name="hr-leave-type-edit"),
    path("hr/leave/settings/rules/new/", leave_views.leave_rule_edit, name="hr-leave-rule-create"),
    path("hr/leave/settings/rules/<int:pk>/edit/", leave_views.leave_rule_edit, name="hr-leave-rule-edit"),
    path("hr/leave/settings/holidays/new/", leave_views.leave_holiday_edit, name="hr-leave-holiday-create"),
    path("hr/leave/settings/holidays/<int:pk>/edit/", leave_views.leave_holiday_edit, name="hr-leave-holiday-edit"),

    # The codes HR prints and posts where crews clock.
    # Where people may clock from. HR's, beside the codes posted there.
    path("hr/where-people-clock/", code_views.office_location, name="hr-office-location"),
    path("hr/codes/", code_views.codes, name="hr-codes"),
    path("hr/codes/new/", code_views.code_create, name="hr-code-create"),
    path("hr/codes/<int:pk>/print/", code_views.code_print, name="hr-code-print"),
    path("hr/codes/<int:pk>/qr.png", code_views.code_image, name="hr-code-image"),
    path("hr/codes/<int:pk>/revoke/", code_views.code_revoke, name="hr-code-revoke"),
]
