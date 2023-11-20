from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Profile, Project  # Import the Project model from your models module

class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['title', 'description'] 

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
    phone = forms.CharField(label='Phone Number', help_text='Enter your phone number.')
    image = forms.ImageField(label='Profile Image', help_text='Upload your profile picture.')
    # Define the projects field within the Meta class
    projects = forms.ModelMultipleChoiceField(
        queryset=Project.objects.all(),  # Retrieve all projects from the database
        widget=forms.CheckboxSelectMultiple,
        required=False,  # Projects are optional
    )

    class Meta:
        model = Profile
        fields = ['phone', 'address', 'image', 'website', 'github', 'twitter', 'instagram', 'facebook', 'projects']
