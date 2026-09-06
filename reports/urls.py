from django.urls import path

from . import views

urlpatterns = [
    path("reports/", views.index, name="reports-index"),
    path("reports/<slug:key>/", views.index, name="reports-detail"),
]
