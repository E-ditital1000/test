from django.urls import path
from .import views 
from .views import CallLogListView, CallLogCreateView, CallLogUpdateView, CallLogDeleteView

urlpatterns = [
    path('index/', views.index, name='dashboard-index'),
    path('products/', views.index, name='dashboard-products'),
    path('products/delete/<int:pk>/', views.product_delete, name='dashboard-products-delete'),
    path('products/detail/<int:pk>/', views.product_detail, name='dashboard-products-detail'),
    path('products/edit/<int:pk>/', views.product_edit, name='dashboard-products-edit'),
    path('my_todo/', views.todo_list_view, name='todo_list'),
    path('generate_pdf/', views.generate_pdf_report, name='generate-pdf-report'),
    path('generate_interaction_pdf/', views.generate_interaction_table_pdf, name='generate-interaction-pdf'),
    path('my_todo/edit/<int:pk>/', views.edit_todo_view, name='edit_todo'),
    path('my_todo/delete/<int:pk>/', views.delete_todo_view, name='delete_todo'),
    path('customers/', views.customers, name='dashboard-customers'),
    path('customers/detail/<int:pk>/', views.customer_detail, name='dashboard-customer-detail'),
    path('order/', views.order, name='dashboard-order'),
    path('create-order/', views.create_order, name='create-order'),
    path('order/delete/<int:order_id>/', views.delete_order, name='delete_order'),
    path('delete/<int:pk>/', views.delete_record, name='delete-record'),
    path('update/<int:pk>/', views.update_record, name='update-record'),
    path('add/', views.add_record, name='add-record'),
    path('record/<int:pk>/', views.customer_record, name='customer-record'),
     # Interaction URLs
    path('interaction/list/', views.interaction_list, name='interaction-list'),
    path('interaction/edit/<int:pk>/', views.interaction_edit, name='interaction-edit'),
    path('interaction/delete/<int:pk>/', views.interaction_delete, name='interaction-delete'),
    path('add-interaction/<int:record_id>/', views.add_interaction, name='add-interaction'),
    # Work URLs
    path('work/list/', views.work_list, name='work-list'),
    path('work/edit/<int:pk>/', views.work_edit, name='work-edit'),
    path('work/delete/<int:pk>/', views.work_delete, name='work-delete'),
     # Include the call log URLs here
    path('list/', CallLogListView.as_view(), name='calllog_list'),
    path('create/', CallLogCreateView.as_view(), name='calllog_create'),
    path('edit/<int:pk>/', CallLogUpdateView.as_view(), name='calllog_edit'),
    path('delete/<int:pk>/', CallLogDeleteView.as_view(), name='calllog_delete'),
    path('generate_calllog_pdf/', views.generate_calllog_pdf, name='generate_calllog_pdf'),
    path('create_record/', views.create_record, name='create_record'),
    # Add a product
    path('add_product/', views.add_product, name='add_product'),

]

#from django.urls import path
#from . import views
#
#urlpatterns = [
#    path('index/', views.index, name='dashboard-index'),
#    path('add_product/', views.add_product, name='add-product'),
#    path('add_employee/', views.add_employee, name='add-employee'),
#    path('add_customer/', views.add_customer, name='add-customer'),
#    path('add_material_request/', views.add_material_request, name='add-material-request'),
#    path('add_work_site_report/', views.add_work_site_report, name='add-work-site-report'),
#    path('update_employee/<int:pk>/', views.update_employee, name='update-employee'),
#    path('delete_employee/<int:pk>/', views.delete_employee, name='delete-employee'),
#    path('update_customer/<int:pk>/', views.update_customer, name='update-customer'),
#    path('delete_customer/<int:pk>/', views.delete_customer, name='delete-customer'),
#   path('update_material_request/<int:pk>/', views.update_material_request, name='update-material-request'),
#    path('delete_material_request/<int:pk>/', views.delete_material_request, name='delete-material-request'),
#    path('update_work_site_report/<int:pk>/', views.update_work_site_report, name='update-work-site-report'),
#    path('delete_work_site_report/<int:pk>/', views.delete_work_site_report, name='delete-work-site-report'),
#    
#]
#    # Add other URL patterns as needed
#
