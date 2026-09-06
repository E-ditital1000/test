from django.urls import path

from . import views

urlpatterns = [
    path("tickets/", views.tickets, name="crm-tickets"),
    path("tickets/new/", views.ticket_create, name="crm-ticket-create"),
    path("tickets/<int:pk>/", views.ticket_detail, name="crm-ticket-detail"),
    path("tickets/<int:pk>/assign/", views.ticket_assign, name="crm-ticket-assign"),
    path("tickets/<int:pk>/update/", views.ticket_update, name="crm-ticket-update"),
    path("tickets/<int:pk>/close/", views.ticket_close, name="crm-ticket-close"),
    path("tickets/<int:pk>/convert/", views.ticket_convert, name="crm-ticket-convert"),

    path("customers/", views.customers, name="crm-customers"),
    path("customers/new/", views.customer_edit, name="crm-customer-create"),
    path("customers/<int:pk>/", views.customer_detail, name="crm-customer-detail"),
    path("customers/<int:pk>/edit/", views.customer_edit, name="crm-customer-edit"),
    path("customers/<int:pk>/sites/new/", views.site_create, name="crm-site-create"),
    path("customers/<int:pk>/contacts/new/", views.contact_create, name="crm-contact-create"),
]
