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

    path("finance/quotations/", views.quotations, name="finance-quotations"),
    path("finance/quotations/new/", views.quotation_create, name="finance-quotation-create"),
    path("finance/quotations/<int:pk>/", views.quotation_detail, name="finance-quotation-detail"),
    path("finance/quotations/<int:pk>/edit/", views.quotation_edit, name="finance-quotation-edit"),
    path("finance/quotations/<int:pk>/items/", views.quotation_line_add, name="finance-quotation-line-add"),
    path("finance/quotation-items/<int:pk>/remove/", views.quotation_line_remove, name="finance-quotation-line-remove"),
    path("finance/quotations/<int:pk>/send/", views.quotation_send, name="finance-quotation-send"),
    path("finance/quotations/<int:pk>/decision/", views.quotation_decide, name="finance-quotation-decide"),
    path("finance/quotations/<int:pk>/invoice/", views.quotation_to_invoice, name="finance-quotation-invoice"),
    path("finance/quotations/<int:pk>/print/", views.quotation_print, name="finance-quotation-print"),

    path("finance/expenses/", views.expenses, name="finance-expenses"),
    path("finance/expenses/new/", views.expense_create, name="finance-expense-create"),

    path("finance/requisitions/", views.requisitions, name="finance-requisitions"),
    path("finance/requisitions/<int:pk>/decide/", views.requisition_decide, name="finance-requisition-decide"),
]
