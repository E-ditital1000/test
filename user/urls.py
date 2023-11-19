from django.urls import path
from . import views

app_name = 'user'  # Add this line to namespace your app URLs

urlpatterns = [
   path('project/add/', views.add_project, name='add_project'),
   path('project/<int:project_id>/delete/', views.delete_project, name='delete_project'),
   path('project/<int:project_id>/', views.project_detail, name='project_detail'),
   path('project/list/', views.project_list, name='project_list'),
   path('project/<int:project_id>/delete/', views.delete_project, name='delete_project'),
]
