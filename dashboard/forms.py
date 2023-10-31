from django import forms
from .models import Product, Order, TodoItem, Record, Interaction, Work
from .models import CallLog

class MonthYearFilterForm(forms.Form):
    month = forms.IntegerField(label='Month', min_value=1, max_value=12)
    year = forms.IntegerField(label='Year', min_value=2000, max_value=2100)

class RecordForm(forms.ModelForm):
    class Meta:
        model = Record
        fields = '__all__'  # You can specify the fields you want to include

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = '__all__'  # You can specify the fields you want to include


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['name', 'customer', 'status', 'order_quantity', 'logo', 'end_on', 'start_on']


class CallLogSearchForm(forms.Form):
    search_query = forms.CharField(label='Search', max_length=100, required=False)

class CallLogForm(forms.ModelForm):
    class Meta:
        model = CallLog
        fields = ['name', 'contact', 'purpose', 'address']

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = '__all__'

class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = '__all__'

class MonthYearForm(forms.Form):
    MONTH_CHOICES = [
        (1, 'January'),
        (2, 'February'),
        (3, 'March'),
        (4, 'April'),
        (5, 'May'),
        (6, 'June'),
        (7, 'July'),
        (8, 'August'),
        (9, 'September'),
        (10, 'October'),
        (11, 'November'),
        (12, 'December'),
    ]

    YEAR_CHOICES = [
        (2023, '2023'),
        (2024, '2024'),
        (2025, '2025'),
        # Add more years as needed
    ]

    month = forms.ChoiceField(choices=MONTH_CHOICES, label='Select Month')
    year = forms.ChoiceField(choices=YEAR_CHOICES, label='Select Year')


class TodoItemForm(forms.ModelForm):
    class Meta:
        model = TodoItem
        fields = '__all__'

class InteractionForm(forms.ModelForm):
    class Meta:
        model = Interaction
        fields = '__all__'

class WorkForm(forms.ModelForm):
    class Meta:
        model = Work
        fields = '__all__'

CUSTOMER_STATUS = (
    ('Completed', 'Completed'),
    ('Pending', 'Pending'),
    ('Processing', 'Processing'),
)

class AddRecordForm(forms.ModelForm):
    first_name = forms.CharField(required=True, widget=forms.TextInput(attrs={"placeholder": "First Name", "class": "form-control"}), label="")
    last_name = forms.CharField(required=True, widget=forms.TextInput(attrs={"placeholder": "Last Name", "class": "form-control"}), label="")
    email = forms.CharField(required=True, widget=forms.TextInput(attrs={"placeholder": "Email", "class": "form-control"}), label="")
    phone = forms.CharField(required=True, widget=forms.TextInput(attrs={"placeholder": "Phone", "class": "form-control"}), label="")
    address = forms.CharField(required=True, widget=forms.TextInput(attrs={"placeholder": "Address", "class": "form-control"}), label="")
    city = forms.CharField(required=True, widget=forms.TextInput(attrs={"placeholder": "City", "class": "form-control"}), label="")
    status = forms.ChoiceField(required=True, choices=CUSTOMER_STATUS, widget=forms.Select(attrs={"class": "form-control"}), label="Status")
    organization = forms.CharField(required=False, widget=forms.TextInput(attrs={"placeholder": "Organization", "class": "form-control"}), label="Organization")
    image = forms.ImageField(required=False, widget=forms.ClearableFileInput(attrs={"class": "form-control"}), label="Image")

    class Meta:
        model = Record
        fields = '__all__'
        exclude = ("user",)


#from django import forms
#from .models import Produce, Employee, Customer, MaterialRequest, WorkSiteReport
#
#class ProduceForm(forms.ModelForm):
#    class Meta:
#        model = Product
#        fields = '__all__'
#
#class EmployeeForm(forms.ModelForm):
#    class Meta:
#        model = Employee
#        fields = '__all__'
#
#class CustomerForm(forms.ModelForm):
#    class Meta:
#        model = Customer
#        fields = '__all__'
#
#class MaterialRequestForm(forms.ModelForm):
#    class Meta:
#        model = MaterialRequest
#        fields = '__all__'
#
#class WorkSiteReportForm(forms.ModelForm):
#    class Meta:
#        model = WorkSiteReport
#        fields = '__all__'
