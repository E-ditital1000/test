from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Profile, Project  # Import the Project model from your models module

class CreateUserForm(UserCreationForm):
    email = forms.EmailField()

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email']


class ProfileUpdateForm(forms.ModelForm):
    projects = forms.ModelMultipleChoiceField(
        queryset=Project.objects.all(),  # Retrieve all projects from the database
        widget=forms.CheckboxSelectMultiple,
        required=False,  # Projects are optional
    )

    class Meta:
        model = Profile
        fields = ['phone', 'address', 'image', 'website', 'github', 'twitter', 'instagram', 'facebook', 'projects']
