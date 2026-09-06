from django.urls import path

from . import views

urlpatterns = [
    path("finance/invoices/", views.invoices, name="finance-invoices"),
    path("finance/invoices/new/", views.invoice_create, name="finance-invoice-create"),
    path("finance/invoices/<int:pk>/", views.invoice_detail, name="finance-invoice-detail"),
    path("finance/invoices/<int:pk>/lines/", views.invoice_line_add, name="finance-invoice-line-add"),
    path("finance/lines/<int:pk>/remove/", views.invoice_line_remove, name="finance-invoice-line-remove"),
    path("finance/invoices/<int:pk>/issue/", views.invoice_issue, name="finance-invoice-issue"),
    path("finance/invoices/<int:pk>/payments/", views.payment_record, name="finance-payment-record"),

    path("finance/expenses/", views.expenses, name="finance-expenses"),
    path("finance/expenses/new/", views.expense_create, name="finance-expense-create"),

    path("finance/requisitions/", views.requisitions, name="finance-requisitions"),
    path("finance/requisitions/<int:pk>/decide/", views.requisition_decide, name="finance-requisition-decide"),
]
