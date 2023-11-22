"""inventoryproject URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from user import views as user_views
from django.conf import settings
from django.conf.urls.static import static
app_name = 'user'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('dashboard.urls')),
    path('register/', user_views.register, name='user-register'),
    path('', auth_views.LoginView.as_view(
        template_name='user/login.html'), name='user-login'),
    path('change-password/', auth_views.PasswordChangeView.as_view(
        template_name='change_password.html',
        success_url='/password_change/done/'
    ), name='change_password'),
    path('profile/', user_views.profile, name='user-profile'),
    path('profile/update/', user_views.profile_update,
         name='user-profile-update'),
    path('project/add/', user_views.add_project, name='add_project'),
    path('project/<int:project_id>/delete/', user_views.delete_project, name='delete_project'),
     path('project/add/', user_views.add_project, name='add_project'),
    path('project/<int:project_id>/', user_views.project_detail, name='project_detail'),
    path('project/list/', user_views.project_list, name='project_list'),
    path('logout/', auth_views.LogoutView.as_view(template_name='user/logout.html'),
         name='user-logout'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL,
                          document_root=settings.MEDIA_ROOT)
