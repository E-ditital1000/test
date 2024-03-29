import uuid  # Add this import statement at the top of your file

from django.db import models
from django.contrib.auth.models import User

class Project(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField()
    # Add more fields related to your project model

    def __str__(self):
        return self.title

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    staff_id = models.CharField(max_length=20, primary_key=True, unique=True)
    address = models.CharField(max_length=200)
    phone = models.CharField(max_length=50)
    image = models.ImageField(default='default.png', upload_to='profile_images')
    website = models.URLField(null=True, blank=True)
    github = models.CharField(max_length=100, null=True, blank=True)
    projects = models.ManyToManyField(Project, blank=True)
    twitter = models.CharField(max_length=100, null=True, blank=True)
    instagram = models.CharField(max_length=100, null=True, blank=True)
    facebook = models.CharField(max_length=100, null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.staff_id:
            # Generate a unique staff_id based on username and timestamp
            self.staff_id = f'{self.user.username}-{uuid.uuid4().hex[:8]}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.user.username}-Profile'
