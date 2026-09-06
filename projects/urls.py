from django.urls import path

from . import views

urlpatterns = [
    path("projects/", views.project_list, name="projects-list"),
    path("projects/<int:pk>/", views.project_detail, name="projects-detail"),
    path("projects/<int:pk>/advance/", views.project_advance, name="projects-advance"),
    path("projects/<int:pk>/tasks/new/", views.task_create, name="projects-task-create"),
    path("tasks/<int:pk>/toggle/", views.task_toggle, name="projects-task-toggle"),
    path("projects/<int:pk>/crew/add/", views.crew_add, name="projects-crew-add"),
    path("crew/<int:pk>/remove/", views.crew_remove, name="projects-crew-remove"),
    path("projects/<int:pk>/documents/", views.document_upload, name="projects-document-upload"),
    path("projects/<int:pk>/requisitions/new/", views.requisition_create, name="projects-requisition-create"),
]
