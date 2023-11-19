from django.urls import reverse
from django.shortcuts import render, redirect, get_object_or_404
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from .forms import CreateUserForm, UserUpdateForm, ProfileUpdateForm, ProjectForm
from .models import Project

# Create your views here.

def register(request):
    if request.method == 'POST':
        form = CreateUserForm(request.POST)
        if form.is_valid():
            user = form.save()
            group = Group.objects.get(name='Customers')
            user.groups.add(group)
            return redirect('user-login')
    else:
        form = CreateUserForm()
    context = {
        'form': form
    }
    return render(request, 'user/register.html', context)

@login_required
def profile(request):
    context = {

    }
    return render(request, 'user/profile.html', context)

@login_required
def profile_update(request):
    if request.method == 'POST':
        u_form = UserUpdateForm(request.POST, instance=request.user)
        p_form = ProfileUpdateForm(
            request.POST, request.FILES, instance=request.user.profile)
        if u_form.is_valid() and p_form.is_valid():
            u_form.save()
            p_form.save()
            return redirect('user-profile')
    else:
        u_form = UserUpdateForm(instance=request.user)
        p_form = ProfileUpdateForm(instance=request.user.profile)

    context = {
        'u_form': u_form,
        'p_form': p_form,
    }
    return render(request, 'user/profile_update.html', context)

@login_required
def add_project(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save()
            # Assign the project to the current user's profile
            request.user.profile.projects.add(project)
            return redirect('user:project_list')
    else:
        form = ProjectForm()
    
    context = {
        'form': form,
    }
    return render(request, 'user/add_project.html', context)

def project_detail(request, project_id):
    project = Project.objects.get(id=project_id)
    # Add logic to display project details if needed
    return render(request, 'user/project_detail.html', {'project': project})

def project_list(request):
    projects = Project.objects.all()
    return render(request, 'user/project_list.html', {'projects': projects})



@login_required
def delete_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    if request.method == 'POST':
        project.delete()
        return redirect('user/project_list')
    
    context = {
        'project': project,
    }
    return render(request, 'user/delete_project.html', context)

