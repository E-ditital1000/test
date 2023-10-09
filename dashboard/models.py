from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

CATEGORY = (
    ('Solar', 'Solar'),
    ('CCTV', 'CCTV'),
    ('Battery', 'Battery'),
)

COSTOMER = (
    ('Completed', 'Completed'),
    ('Pending', 'Pending'),
    ('Processing', 'Processing'),
)

class Product(models.Model):
    name = models.CharField(max_length=100, null=True)
    image = models.ImageField(blank=True, upload_to='media', null=True)
    quantity = models.PositiveIntegerField(null=True)
    category = models.CharField(max_length=50, choices=CATEGORY, null=True)

    def __str__(self):
        return f'{self.name} - {self.category} - {self.quantity}'

class Order(models.Model):
    name = models.ForeignKey(Product, on_delete=models.CASCADE, null=True)
    customer = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    status = models.CharField(max_length=50, choices=COSTOMER, null=True)
    order_quantity = models.PositiveIntegerField(null=True)
    logo = models.ImageField(blank=True, upload_to='media', null=True)
    end_on = models.DateTimeField(default=timezone.now)
    start_on = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f'{self.customer}-{self.name}'
    
class TodoItem(models.Model):
    title = models.CharField(max_length=250)
    status = models.BooleanField(default=False)

    def __str__(self):
        return self.title

class Record(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    email = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)
    address = models.CharField(max_length=100)
    city = models.CharField(max_length=50)
    status = models.CharField(max_length=50, choices=COSTOMER, null=True)
    organization = models.CharField(max_length=100, null=True)
    image = models.ImageField(blank=True, upload_to='media', null=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

class Interaction(models.Model):
    record = models.ForeignKey(Record, on_delete=models.CASCADE)
    interaction_type = models.CharField(max_length=50)  # Call, Email, WhatsApp, etc.
    date = models.DateField()
    details = models.TextField()
    discussion_points = models.TextField(blank=True, null=True)
    follow_up_required = models.BooleanField(default=False)
    follow_up_date = models.DateField(blank=True, null=True)
    
    class Meta:
        ordering = ['-date']

class Work(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)  # User who did the work
    record = models.ForeignKey(Record, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    description = models.TextField()
    start_date = models.DateTimeField(auto_now_add=True) 
    due_date = models.DateField()
    completed = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-due_date']


class CallLog(models.Model):
    name = models.CharField(max_length=100)
    contact = models.CharField(max_length=15)
    purpose = models.TextField()
    address = models.TextField()
    date = models.DateField(auto_now_add=True,  null=True)  # Automatically set the date when a log is created

    def __str__(self):
        return self.name
        

    
#from django.db import models
#from django.contrib.auth.models import User
#from django.utils import timezone
#
#CATEGORY_CHOICES = [
#    ('Solar', 'Solar'),
#    ('CCTV', 'CCTV'),
#    ('Battery', 'Battery'),
#]
#
#class Category(models.Model):
#    name = models.CharField(max_length=50, default='Solar', choices=CATEGORY_CHOICES)
#
#    def __str__(self):
#        return self.name
#
#class Produce(models.Model):
#    name = models.CharField(max_length=100, null=True)
#    image = models.ImageField(blank=True, upload_to='media', default='default.png', null=True)
#    quantity = models.PositiveIntegerField(default=0)
#    category = models.ForeignKey(Category, on_delete=models.CASCADE, default=1)  # Set the default to the ID of the 'Solar' category
#
#    def __str__(self):
#        return f'{self.name} - {self.category.name} - {self.quantity}'
#
#class Employee(models.Model):
#    user = models.OneToOneField(User, on_delete=models.CASCADE)
#    position = models.CharField(max_length=100, null=True)
#    contact_phone = models.CharField(max_length=15, null=True)
#    contact_email = models.EmailField()
#    # You can add more fields specific to employees
#
#    def __str__(self):
#        return self.user.username
#
#COSTOMER = (
#    ('Completed', 'Completed'),
#    ('Pending', 'Pending'),
#    ('Processing', 'Processing'),
#)
#
#class Customer(models.Model):
#    user = models.OneToOneField(User, on_delete=models.CASCADE)
#    organization = models.CharField(max_length=100, null=True)
#    contact_person = models.CharField(max_length=100, null=True)
#    contact_phone = models.CharField(max_length=15, null=True)
#    contact_email = models.EmailField()
#    address =  models.CharField(max_length=100,  null=True)
#    status = models.CharField(max_length=50, choices=COSTOMER, null=True)
#    image = models.ImageField(blank=True, upload_to='media', null=True)
#    # You can add more fields specific to customers
#
#    def __str__(self):
#        return self.user.username
#
#class MaterialRequest(models.Model):
#    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
#    product = models.ForeignKey(Product, on_delete=models.CASCADE)
#    quantity = models.PositiveIntegerField()
#    request_date = models.DateTimeField(default=timezone.now)
#    purpose = models.CharField(max_length=100, null=True)
#
#    def __str__(self):
#        return f'{self.employee.user.username} - {self.product.name} - {self.quantity}'
#
#class WorkSiteReport(models.Model):
#    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
#    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
#    report_text = models.TextField()
#    assessment_date = models.DateTimeField(default=timezone.now)
#    # Add more fields as needed for the report
#
#    def __str__(self):
#        return f'Report for {self.customer.user.username} by {self.employee.user.username}'
#
#
#