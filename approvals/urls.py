from django.urls import path

from . import views

urlpatterns = [
    path("approvals/", views.queue, name="approvals-queue"),
    path("approvals/assessments/<int:pk>/", views.assessment_review, name="approvals-assessment-review"),
    path("approvals/assessments/<int:pk>/decide/", views.assessment_decide, name="approvals-assessment-decide"),
]
