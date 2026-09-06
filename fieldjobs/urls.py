from django.urls import path

from . import views

urlpatterns = [
    # The technician's own surfaces.
    path("field-jobs/", views.my_jobs, name="fieldjobs-my-jobs"),
    path("field-jobs/<int:pk>/", views.job_detail, name="fieldjobs-job-detail"),
    path("field-jobs/<int:pk>/check-in/", views.check_in, name="fieldjobs-check-in"),
    path("field-jobs/<int:pk>/assessment/", views.assessment_form, name="fieldjobs-assessment"),
    path("field-jobs/<int:pk>/assessment/submit/", views.assessment_submit, name="fieldjobs-assessment-submit"),

    # The office side.
    path("field-jobs/schedule/", views.schedule, name="fieldjobs-schedule"),
    path("projects/<int:project_pk>/field-jobs/new/", views.schedule, name="fieldjobs-schedule-for-project"),
]
