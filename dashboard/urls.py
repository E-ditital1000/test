from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="dashboard-index"),
    path("command-view/", views.command_view, name="dashboard-command-view"),
    path("search/", views.global_search, name="global-search"),
    path("my-tasks/", views.my_tasks, name="dashboard-my-tasks"),
    # Off the dashboard, by A-1's instruction, and onto a screen of its own.
    path("attention/", views.attention, name="dashboard-attention"),
]
