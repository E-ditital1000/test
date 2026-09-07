from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="dashboard-index"),
    path("command-view/", views.command_view, name="dashboard-command-view"),
    path("search/", views.global_search, name="global-search"),
]
