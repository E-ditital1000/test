from django.contrib import admin
from .models import Product, Order, TodoItem, Record, Interaction, Work, CallLog

# Register your models here.
admin.site.register(Product)
admin.site.register(Order)
admin.site.register(TodoItem)
admin.site.register(Record)
admin.site.register(Interaction)
admin.site.register(Work)
admin.site.register(CallLog)



#from django.contrib import admin
#from .models import Produce, Employee, Customer, MaterialRequest, WorkSiteReport
#
## Register your models here.
#admin.site.register(Produce)
#admin.site.register(Employee)
#admin.site.register(Customer)
#admin.site.register(MaterialRequest)
#admin.site.register(WorkSiteReport)
