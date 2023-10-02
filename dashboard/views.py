from django.shortcuts import render, redirect, get_object_or_404
from datetime import datetime
from io import BytesIO
from django.http import HttpResponse
from django.contrib.auth.models import User
from django.template.loader import get_template
from .models import Product, Order, TodoItem, Interaction, Work
from .forms import ProductForm,TodoItemForm, AddRecordForm, InteractionForm, WorkForm, OrderForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .decorators import auth_users, allowed_users
from .models import Record
from django.db.models import Q
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.template.loader import get_template

from django.http import HttpResponse
from io import BytesIO
from .models import CallLog
from .forms import CallLogForm
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle, TA_CENTER  # Import TA_CENTER
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    KeepInFrame,
    PageBreak,
    PageTemplate,
)
from io import BytesIO
from django.http import HttpResponse
from django.conf import settings
from .models import Work


@login_required(login_url='user-login')
def generate_pdf_report(request):

        # Get the selected month and year from the form
    selected_month = request.POST.get('month')
    selected_year = request.POST.get('year')

    # Validate that both month and year are provided
    if not selected_month or not selected_year:
        return HttpResponse("Please select both a month and a year to generate the report.")

    # Convert the selected month and year to integers
    selected_month = int(selected_month)
    selected_year = int(selected_year)

    # Get the current user
    user = request.user

    # Filter works based on selected month and year
    filtered_works = Work.objects.filter(
        start_date__month=selected_month,
        start_date__year=selected_year,
        user=user
    )
    # Create a file-like buffer to receive PDF data.
    buffer = BytesIO()

    # Create the PDF object, using the BytesIO buffer as its "file."
    doc = SimpleDocTemplate(
        buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36
    )

    # Define a list to hold the elements you want to include in the PDF
    elements = []

    # Add the image
    image_path = 'static/img/atech.png'  # Replace with the actual image path
    image = Image(image_path, width=70, height=70)
    image._offs_x = -230
    image._offs_y = -50
    elements.append(image)

    # Create a custom style for the title
    title_style = ParagraphStyle(
        name='TitleStyle',
        fontSize=14,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold',
    )

    # Add the heading
    heading = "A1technical Engineering Solution\nNagbw's Town Junction, ELWA"
    heading_style = TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 14),  # Adjust font size as needed
    ])
    heading_table = Table([[heading]], style=heading_style)
    elements.append(heading_table)

    elements.append(Spacer(0, 0.1 * inch))
    # Add the title
    user = request.user  # Get the current user
    title_text = f"{user.username}'s Work List - for the month of September"
    title = Paragraph(title_text.upper(), title_style)
    elements.append(title)

    # Add some vertical space
    elements.append(Spacer(0, 0.1 * inch))

    # Define a custom style for the table cells with reduced font size and padding
    table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 0), (-1, -1), 8),  # Set a smaller font size
        ('LEFTPADDING', (0, 0), (-1, -1), 6),  # Set left padding
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),  # Set right padding
        ('TOPPADDING', (0, 0), (-1, -1), 4),  # Set top padding
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),  # Set bottom padding
    ])

    # Create a list to hold the table data
    data = []
    data.append(["Work Title", "Description", "Technician", "Customer", "Start Date", "Due Date", "Completed"])

    # Add work list data to the table
    works = Work.objects.all()  # You can add filtering if needed
    for work in works:
        data.append([work.title, work.description, str(work.user), str(work.record),
                     str(work.start_date), str(work.due_date), "Completed" if work.completed else "Not Completed"])

    # Adjust column widths based on the available page width
    max_table_width = letter[0] - doc.leftMargin - doc.rightMargin
    col_widths = [max_table_width * 0.1, max_table_width * 0.2, max_table_width * 0.1,
                  max_table_width * 0.1, max_table_width * 0.15, max_table_width * 0.15, max_table_width * 0.1]

    # Create a table and set its style
    table = Table(data, colWidths=col_widths)
    table.setStyle(table_style)

    # Append elements to the list
    elements.append(Spacer(1, 12))  # Add some vertical space
    elements.append(table)  # Add the table
 # Add some vertical space
    elements.append(Spacer(0, 0.1 * inch))
        # Create a signing area style
    signing_area_style = TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 18),  # Increase padding between lines
        
        ('BOTTOMPADDING', (0, 1), (-1, 1), 6),  # Adjust padding between lines
       
    ])

    # Create a signing area
    signing_area_data = [
        [Paragraph("<b>Date:</b>_____________________", title_style), None],
        [Paragraph("<b>Signature:</b> ___________________", title_style), None],
    ]
    signing_area_table = Table(signing_area_data, colWidths=[250, 250])
    signing_area_table.setStyle(signing_area_style)
    elements.append(signing_area_table)

    # Build the PDF document
    doc.build(elements)

    # FileResponse sets the Content-Disposition header so that browsers
    # present the option to save the file.
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="work_list_report.pdf"'
    return response

@login_required(login_url='user-login')
def generate_interaction_table_pdf(request):
    interactions = Interaction.objects.all()  #
    # Create a file-like buffer to receive PDF data.
    buffer = BytesIO()

    # Create the PDF object, using the BytesIO buffer as its "file."
    doc = SimpleDocTemplate(
        buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36
    )

    # Define a list to hold the elements you want to include in the PDF
    elements = []

    # Add the image
    image_path = 'static/img/atech.png'  # Replace with the actual image path
    image = Image(image_path, width=70, height=70)
    image._offs_x = -230
    image._offs_y = -50
    elements.append(image)

    # Create a custom style for the title
    title_style = ParagraphStyle(
        name='TitleStyle',
        fontSize=14,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold',
    )

    # Add the heading
    heading = "A1technical Engineering Solution\nNagbw's Town Junction, ELWA"
    heading_style = TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 14),  # Adjust font size as needed
    ])
    heading_table = Table([[heading]], style=heading_style)
    elements.append(heading_table)

    elements.append(Spacer(0, 0.1 * inch))
    # Add the title
    user = request.user  # Get the current user
    title_text = f"{user.username}'s Interaction_list - for the month of September"
    title = Paragraph(title_text.upper(), title_style)
    elements.append(title)

    # Add some vertical space
    elements.append(Spacer(0, 0.1 * inch))

    # Define a custom style for the table cells with reduced font size and padding
    table_style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 0), (-1, -1), 8),  # Set a smaller font size
        ('LEFTPADDING', (0, 0), (-1, -1), 6),  # Set left padding
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),  # Set right padding
        ('TOPPADDING', (0, 0), (-1, -1), 4),  # Set top padding
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),  # Set bottom padding
    ])
   # Create a list to hold the table data
    data = []
    data.append(["Customer", "Interaction Type", "Date", "Details", "Discussion Points", "Follow-up", "Follow-up Date"])

    # Add interaction data to the table
    for interaction in interactions:
        data.append([
            interaction.record,
            interaction.interaction_type,
            str(interaction.date),
            interaction.details,
            interaction.discussion_points,
            "Yes" if interaction.follow_up_required else "No",
            str(interaction.follow_up_date) if interaction.follow_up_date else ""
        ])

    # Adjust column widths based on the available page width
    max_table_width = letter[0] - doc.leftMargin - doc.rightMargin
    col_widths = [max_table_width * 0.1, max_table_width * 0.2, max_table_width * 0.1,
                  max_table_width * 0.1, max_table_width * 0.15, max_table_width * 0.15, max_table_width * 0.1]

    # Create a table and set its style
    table = Table(data, colWidths=col_widths)
    table.setStyle(table_style)

    # Append elements to the list
    elements.append(Spacer(1, 12))  # Add some vertical space
    elements.append(table)  # Add the table
 # Add some vertical space
    elements.append(Spacer(0, 0.1 * inch))
        # Create a signing area style
    signing_area_style = TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 18),  # Increase padding between lines
        
        ('BOTTOMPADDING', (0, 1), (-1, 1), 6),  # Adjust padding between lines
       
    ])

    # Create a signing area
    signing_area_data = [
        [Paragraph("<b>Date:</b>_____________________", title_style), None],
        [Paragraph("<b>Signature:</b> ___________________", title_style), None],
    ]
    signing_area_table = Table(signing_area_data, colWidths=[250, 250])
    signing_area_table.setStyle(signing_area_style)
    elements.append(signing_area_table)

    # Build the PDF document
    doc.build(elements)

    # FileResponse sets the Content-Disposition header so that browsers
    # present the option to save the file.
    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="interaction_List.pdf"'
    return response



@login_required(login_url='user-login')
def index(request):
    products = Product.objects.all()
    product_count = products.count()
    records = Record.objects.all()
    customer_count = User.objects.filter(groups=2).count()
    order_count = Order.objects.all().count()
    product_quantity = Product.objects.filter(name='')

    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            form.save()
            product_name = form.cleaned_data.get('name')
            messages.success(request, f'{product_name} has been added')
            return redirect('dashboard-index')
    else:
        form = ProductForm()

    context = {
        'products': products,
        'form': form,
        'records': records,
        'customer_count': customer_count,
        'product_count': product_count,
        'order_count': order_count,
    }
    return render(request, 'dashboard/index.html', context)




@login_required(login_url='user-login')
def customer_count(request):
    customer_count = User.objects.filter(groups=2).count()
    context = {
        'customer_count': customer_count,
    }
    return render(request, 'dashboard/customer_count.html', context)

@login_required(login_url='user-login')
def product_count(request):
    product_count = Product.objects.all().count()
    context = {
        'product_count': product_count,
    }
    return render(request, 'dashboard/product_count.html', context)

@login_required(login_url='user-login')
def order_count(request):
    order_count = Order.objects.all().count()
    context = {
        'order_count': order_count,
    }
    return render(request, 'dashboard/order_count.html', context)

@login_required(login_url='user-login')
def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)
    context = {
        'product': product,
    }
    return render(request, 'dashboard/product_detail.html', context)
@login_required(login_url='user-login')
def interaction_list(request):
    # Get the search query from the form input
    search_query = request.GET.get('q')

    # Filter interactions based on the search query if provided
    interactions = Interaction.objects.all()
    if search_query:
        # Filter interactions where the customer's name, email, or phone matches the query
        interactions = interactions.filter(
            Q(record__first_name__icontains=search_query) |
            Q(record__last_name__icontains=search_query) |
            Q(record__email__icontains=search_query) |
            Q(record__phone__icontains=search_query)
        )

    context = {
        'interactions': interactions,
        'search_query': search_query,  # Pass the search query back to the template
    }

    return render(request, 'dashboard/interaction_list.html', context)
@login_required(login_url='user-login')
def interaction_edit(request, pk):
    interaction = get_object_or_404(Interaction, pk=pk)
    if request.method == 'POST':
        form = InteractionForm(request.POST, instance=interaction)
        if form.is_valid():
            form.save()
            messages.success(request, 'Interaction has been updated')
            return redirect('interaction-list')
    else:
        form = InteractionForm(instance=interaction)
    context = {
        'form': form,
    }
    return render(request, 'dashboard/interaction_edit.html', context)

@login_required(login_url='user-login')
def add_interaction(request, record_id):
    # Retrieve the Record object based on the record_id
    record = get_object_or_404(Record, pk=record_id)

    if request.method == 'POST':
        form = InteractionForm(request.POST)
        if form.is_valid():
            # Save the new Interaction object with the associated Record
            interaction = form.save(commit=False)
            interaction.record = record
            interaction.save()
            messages.success(request, 'Interaction has been added')
            return redirect('dashboard/interaction-list')  # Redirect to the interaction list view
    else:
        form = InteractionForm()

    context = {
        'form': form,
        'record_id': record_id,  # Include 'record_id' in the context
    }
    return render(request, 'interaction_form.html', context)

    context = {
        'form': form,
        'record': record,
    }
    return render(request, 'dashboard/interaction_form.html', context)

@login_required(login_url='user-login')
def interaction_delete(request, pk):
    interaction = get_object_or_404(Interaction, pk=pk)
    if request.method == 'POST':
        interaction.delete()
        messages.success(request, 'Interaction has been deleted')
        return redirect('interaction-list')
    context = {
        'interaction': interaction,
    }
    return render(request, 'dashboard/interaction_delete.html', context)



@login_required(login_url='user-login')
def work_list(request):
    search_query = request.GET.get('q')
    works = Work.objects.filter(user=request.user)  # Filter works for the current user
    
    if request.user.is_superuser:
        works = Work.objects.all()  # Superuser sees all works
    
    if search_query:
        works = works.filter(
            Q(record__first_name__icontains=search_query) |
            Q(record__last_name__icontains=search_query) |
            Q(record__email__icontains=search_query) |
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    if request.method == 'POST':
        work_form = WorkForm(request.POST)
        if work_form.is_valid():
            new_work = work_form.save(commit=False)
            new_work.user = request.user
            new_work.save()
    else:
        work_form = WorkForm()

    context = {
        'works': works,
        'work_form': work_form,
    }
    return render(request, 'dashboard/work_list.html', context)

@login_required(login_url='user-login')
def work_edit(request, pk):
    work = get_object_or_404(Work, pk=pk)
    if not request.user.is_superuser and work.user != request.user:
        messages.error(request, 'You do not have permission to edit this work.')
        return redirect('work-list')
    
    if request.method == 'POST':
        form = WorkForm(request.POST, instance=work)
        if form.is_valid():
            form.save()
            messages.success(request, 'Work has been updated')
            return redirect('work-list')
    else:
        form = WorkForm(instance=work)
    context = {
        'form': form,
    }
    return render(request, 'dashboard/work_edit.html', context)

@login_required(login_url='user-login')
def work_delete(request, pk):
    work = get_object_or_404(Work, pk=pk)
    if not request.user.is_superuser and work.user != request.user:
        messages.error(request, 'You do not have permission to delete this work.')
        return redirect('work-list')
    
    if request.method == 'POST':
        work.delete()
        messages.success(request, 'Work has been deleted')
        return redirect('work-list')
    context = {
        'work': work,
    }
    return render(request, 'dashboard/work_delete.html', context)

#@login_required(login_url='user-login')
#def work_edit(request, pk):
#    work = get_object_or_404(Work, pk=pk, user=request.user)  # Ensure the work belongs to the user
#    if request.method == 'POST':
#        form = WorkForm(request.POST, instance=work)
#        if form.is_valid():
#            form.save()
#            messages.success(request, 'Work has been updated')
#            return redirect('work-list')
#    else:
#        form = WorkForm(instance=work)
#    context = {
#        'form': form,
#    }
#    return render(request, 'dashboard/work_edit.html', context)
#
#@login_required(login_url='user-login')
#def work_delete(request, pk):
#    work = get_object_or_404(Work, pk=pk, user=request.user)  # Ensure the work belongs to the user
#    if request.method == 'POST':
#        work.delete()
#        messages.success(request, 'Work has been deleted')
#        return redirect('work-list')
#    context = {
#        'work': work,
#    }
#    return render(request, 'dashboard/work_delete.html', context)
#

#@login_required(login_url='user-login')
#def work_list(request):
#    works = Work.objects.all()
#    if request.method == 'POST':
#        work_form = WorkForm(request.POST)
#        if work_form.is_valid():
#            work_form.save()
#    else:
#        work_form = WorkForm()
#
#    context = {
#        'works': works,
#        'work_form': work_form,
#    }
#    return render(request, 'dashboard/work_list.html', context)



#
#
#
#
##@login_required(login_url='user-login')
#def index(request):
#    products = Product.objects.all()
#    product_count = products.count()
#    records = Record.objects.all()
#    customer_count = User.objects.filter(groups=2).count()
#    order_count = Order.objects.all().count()
#    product_quantity = Product.objects.filter(name='')
#
#    if request.method == 'POST':
#        form = ProductForm(request.POST)
#        if form.is_valid():
#            form.save()
#            product_name = form.cleaned_data.get('name')
#            messages.success(request, f'{product_name} has been added')
#            return redirect('dashboard-index')
#    else:
#        form = ProductForm()
#
#    context = {
#        'products': products,
#        'form': form,
#        'records': records,
#        'customer_count': customer_count,
#        'product_count': product_count,
#        'order_count': order_count,
#    }
#    return render(request, 'dashboard/index.html', context)
#
#@login_required(login_url='user-login')
#def product_detail(request, pk):
#    context = {
#
#    }
#    return render(request, 'dashboard/products_detail.html', context)



from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from .models import CallLog
from .forms import CallLogForm


def generate_calllog_pdf(request):
    buffer = BytesIO()
    
    calllogs = CallLog.objects.all()

    # Define the image path
    image_path = 'static/img/atech.png'

 

    # Create the PDF object, using the BytesIO buffer as its "file."
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    elements = []

    # Add the image
    image = Image(image_path, width=200, height=100)
    elements.append(image)

# Add the heading
    heading = "A1technical Engineering Solution\nNagbw's Town Junction, ELWA"
    heading_style = TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 12),  # Adjust font size as needed
    ])
    heading_table = Table([[heading]], style=heading_style)
    elements.append(heading_table)

  

    # Add a title for the PDF
    styles = getSampleStyleSheet()
    title = Paragraph("Call_Log Report", styles['Title'])
    elements.append(title)

    # Define the table data (you can customize this as needed)
    data = [['Name', 'Contact', 'Purpose', 'Address', 'Date']]
    for calllog in calllogs:
        data.append([calllog.name, calllog.contact, calllog.purpose, calllog.address, calllog.date])

    # Create a table and style it
    table = Table(data)
    style = TableStyle([
      ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTSIZE', (0, 0), (-1, -1), 8),  # Set a smaller font size
        ('LEFTPADDING', (0, 0), (-1, -1), 6),  # Set left padding
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),  # Set right padding
        ('TOPPADDING', (0, 0), (-1, -1), 4),  # Set top padding
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),  # Set bottom padding
    ])

    table.setStyle(style)
    elements.append(table)

    # Build the PDF document in the buffer
    doc.build(elements)

    # Get the value of the BytesIO buffer
    pdf_data = buffer.getvalue()

    # Close the buffer
    buffer.close()

    # Create the HttpResponse with the PDF data
    response = HttpResponse(pdf_data, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="calllog.pdf"'

    return response




class CallLogListView(ListView):
    model = CallLog
    template_name = 'dashboard/calllog_list.html'
    context_object_name = 'calllogs'

class CallLogCreateView(CreateView):
    model = CallLog
    form_class = CallLogForm
    template_name = 'dashboard/calllog_form.html'
    success_url = reverse_lazy('calllog_list')

class CallLogUpdateView(UpdateView):
    model = CallLog
    form_class = CallLogForm
    template_name = 'dashboard/calllog_form.html'
    success_url = reverse_lazy('calllog_list')

class CallLogDeleteView(DeleteView):
    model = CallLog  # Correctly specify the model
    template_name = 'dashboard/calllog_confirm_delete.html'
    success_url = reverse_lazy('calllog_list')


from .forms import CallLogSearchForm  # Import the search form

class CallLogListView(ListView):
    model = CallLog
    template_name = 'dashboard/calllog_list.html'
    context_object_name = 'calllogs'

    def get_queryset(self):
        queryset = super().get_queryset()
        search_query = self.request.GET.get('search_query')

        if search_query:
            queryset = queryset.filter(name__icontains=search_query)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_form'] = CallLogSearchForm(self.request.GET)
        return context


@login_required(login_url='user-login')
@allowed_users(allowed_roles=['Admin'])
def customers(request):
    customer = User.objects.filter(groups=2)
    customer_count = customer.count()
    product = Product.objects.all()
    product_count = product.count()
    order = Order.objects.all()
    order_count = order.count()
    context = {
        'customer': customer,
        'customer_count': customer_count,
        'product_count': product_count,
        'order_count': order_count,
    }
    return render(request, 'dashboard/customers.html', context)

@login_required(login_url='user-login')
@allowed_users(allowed_roles=['Admin'])
def customer_detail(request, pk):
    customer = User.objects.filter(groups=2)
    customer_count = customer.count()
    product = Product.objects.all()
    product_count = product.count()
    order = Order.objects.all()
    order_count = order.count()
    customers = User.objects.get(id=pk)
    context = {
        'customers': customers,
        'customer_count': customer_count,
        'product_count': product_count,
        'order_count': order_count,

    }
    return render(request, 'dashboard/customers_detail.html', context)

@login_required(login_url='user-login')
@allowed_users(allowed_roles=['Admin'])
def product_edit(request, pk):
    item = Product.objects.get(id=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            return redirect('dashboard-products')
    else:
        form = ProductForm(instance=item)
    context = {
        'form': form,
    }
    return render(request, 'dashboard/products_edit.html', context)

@login_required(login_url='user-login')
@allowed_users(allowed_roles=['Admin'])
def product_delete(request, pk):
    item = Product.objects.get(id=pk)
    if request.method == 'POST':
        item.delete()
        return redirect('dashboard-products')
    context = {
        'item': item
    }
    return render(request, 'dashboard/products_delete.html', context)

from django.http import JsonResponse

def update_quantity(request, pk):
    if request.method == 'POST':
        item = Product.objects.get(id=pk)
        action = request.POST.get('action')  # 'add' or 'deduct'
        quantity_change = int(request.POST.get('quantity_change'))
        
        if action == 'add':
            item.quantity += quantity_change
        elif action == 'deduct':
            item.quantity -= quantity_change
        
        item.save()
        
        data = {
            'new_quantity': item.quantity,
        }
        
        return JsonResponse(data)

@login_required(login_url='user-login')
def order(request):
    order = Order.objects.all()
    order_count = order.count()
    customer = User.objects.filter(groups=2)
    customer_count = customer.count()
    product = Product.objects.all()
    product_count = product.count()

    context = {
        'order': order,
        'customer_count': customer_count,
        'product_count': product_count,
        'order_count': order_count,
    }
    return render(request, 'dashboard/order.html', context)



def edit_todo_view(request, pk):
    todo_item = get_object_or_404(TodoItem, pk=pk)
    
    if request.method == 'POST':
        form = TodoItemForm(request.POST, instance=todo_item)
        if form.is_valid():
            form.save()
            return redirect('todo_list')

    form = TodoItemForm(instance=todo_item)
    context = {'form': form}
    return render(request, 'dashboard/edit_todo.html', context)

def delete_todo_view(request, pk):
    todo_item = get_object_or_404(TodoItem, pk=pk)
    
    if request.method == 'POST':
        todo_item.delete()
        return redirect('todo_list')

    context = {'todo_item': todo_item}
    return render(request, 'dashboard/delete_todo.html', context)

def todo_list_view(request):
    todo_items = TodoItem.objects.all()
    form = TodoItemForm()

    if request.method == 'POST':
        form = TodoItemForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('todo_list')

    context = {'todo_items': todo_items, 'form': form}
    return render(request, 'dashboard/todo_list.html', context)

@login_required(login_url='user-login')
def customer_record(request, pk):
    customer_record = get_object_or_404(Record, id=pk)
    return render(request, 'record.html', {'customer_record': customer_record})

def order(request):
    orders = Order.objects.all()

    status_counts = orders.values('status').annotate(count=Count('status'))

    context = {
        'orders': orders,
        'status_counts': status_counts,
    }
    return render(request, 'dashboard/order.html', context)

def delete_record(request, pk):
	if request.user.is_authenticated:
		delete_it = Record.objects.get(id=pk)
		delete_it.delete()
		messages.success(request, "Record Deleted Successfully...")
		return redirect('dashboard-index')
	else:
		messages.success(request, "You Must Be Logged In To Do That...")
		return redirect('dashboard-index')


def add_record(request):
	form = AddRecordForm(request.POST or None)
	if request.user.is_authenticated:
		if request.method == "POST":
			if form.is_valid():
				add_record = form.save()
				messages.success(request, "Record Added...")
				return redirect('dashboard-index')
		return render(request, 'add_record.html', {'form':form})
	else:
		messages.success(request, "You Must Be Logged In...")
		return redirect('dashboard-index')


def update_record(request, pk):
	if request.user.is_authenticated:
		current_record = Record.objects.get(id=pk)
		form = AddRecordForm(request.POST or None, instance=current_record)
		if form.is_valid():
			form.save()
			messages.success(request, "Record Has Been Updated!")
			return redirect('dashboard-index')
		return render(request, 'update_record.html', {'form':form})
	else:
		messages.success(request, "You Must Be Logged In...")
		return redirect('dashboard-index')


#@login_required(login_url='user-login')
#def add_product(request):
#    if request.method == 'POST':
#        form = ProductForm(request.POST)
#        if form.is_valid():
#            form.save()
#            product_name = form.cleaned_data.get('name')
#            messages.success(request, f'{product_name} has been added')
#            return redirect('dashboard-index')
#    else:
#        form = ProductForm()
#
#    context = {
#        'form': form,
#    }
#    return render(request, 'add_product.html', context)
#
#@login_required(login_url='user-login')
#def add_employee(request):
#    if request.method == 'POST':
#        form = EmployeeForm(request.POST)
#        if form.is_valid():
#            form.save()
#            employee_name = form.cleaned_data.get('user').username
#            messages.success(request, f'Employee {employee_name} has been added')
#            return redirect('dashboard-index')
#    else:
#        form = EmployeeForm()
#
#    context = {
#        'form': form,
#    }
#    return render(request, 'add_employee.html', context)
#
#@login_required(login_url='user-login')
#def add_customer(request):
#    if request.method == 'POST':
#        form = CustomerForm(request.POST)
#        if form.is_valid():
#            form.save()
#            customer_name = form.cleaned_data.get('user').username
#            messages.success(request, f'Customer {customer_name} has been added')
#            return redirect('dashboard-index')
#    else:
#        form = CustomerForm()
#
#    context = {
#        'form': form,
#    }
#    return render(request, 'add_customer.html', context)
#
#@login_required(login_url='user-login')
#def add_material_request(request):
#    if request.method == 'POST':
#        form = MaterialRequestForm(request.POST)
#        if form.is_valid():
#            form.save()
#            messages.success(request, 'Material request has been added')
#            return redirect('dashboard-index')
#    else:
#        form = MaterialRequestForm()
#
#    context = {
#        'form': form,
#    }
#    return render(request, 'add_material_request.html', context)
#
#@login_required(login_url='user-login')
#def add_work_site_report(request):
#    if request.method == 'POST':
#        form = WorkSiteReportForm(request.POST)
#        if form.is_valid():
#            form.save()
#            messages.success(request, 'Work site report has been added')
#            return redirect('dashboard-index')
#    else:
#        form = WorkSiteReportForm()
#
#    context = {
#        'form': form,
#    }
#    return render(request, 'add_work_site_report.html', context)
#
#@login_required(login_url='user-login')
#def update_product(request, pk):
#    product = get_object_or_404(Product, pk=pk)
#    if request.method == 'POST':
#        form = ProductForm(request.POST, instance=product)
#        if form.is_valid():
#            form.save()
#            messages.success(request, 'Product has been updated')
#            return redirect('dashboard-index')
#    else:
#        form = ProductForm(instance=product)
#    
#    context = {
#        'form': form,
#        'item': product,
#    }
#    return render(request, 'update_item.html', context)
#
#@login_required(login_url='user-login')
#def delete_product(request, pk):
#    product = get_object_or_404(Product, pk=pk)
#    if request.method == 'POST':
#        product.delete()
#        messages.success(request, 'Product has been deleted')
#        return redirect('dashboard-index')
#    
#    context = {
#        'item': product,
#    }
#    return render(request, 'delete_item.html', context)
#@login_required(login_url='user-login')
#def update_employee(request, pk):
#    employee = get_object_or_404(Employee, pk=pk)
#    if request.method == 'POST':
#        form = EmployeeForm(request.POST, instance=employee)
#        if form.is_valid():
#            form.save()
#            messages.success(request, 'Employee has been updated')
#            return redirect('dashboard-index')
#    else:
#        form = EmployeeForm(instance=employee)
#    
#    context = {
#        'form': form,
#        'item': employee,
#    }
#    return render(request, 'update_item.html', context)
#
#@login_required(login_url='user-login')
#def delete_employee(request, pk):
#    employee = get_object_or_404(Employee, pk=pk)
#    if request.method == 'POST':
#        employee.delete()
#        messages.success(request, 'Employee has been deleted')
#        return redirect('dashboard-index')
#    
#    context = {
#        'item': employee,
#    }
#    return render(request, 'delete_item.html', context)
#
## ... Your existing views ...
#
#@login_required(login_url='user-login')
#def update_material_request(request, pk):
#    material_request = get_object_or_404(MaterialRequest, pk=pk)
#    if request.method == 'POST':
#        form = MaterialRequestForm(request.POST, instance=material_request)
#        if form.is_valid():
#            form.save()
#            messages.success(request, 'Material request has been updated')
#            return redirect('dashboard-index')
#    else:
#        form = MaterialRequestForm(instance=material_request)
#    
#    context = {
#        'form': form,
#        'item': material_request,
#    }
#    return render(request, 'update_item.html', context)
#
#@login_required(login_url='user-login')
#def delete_material_request(request, pk):
#    material_request = get_object_or_404(MaterialRequest, pk=pk)
#    if request.method == 'POST':
#        material_request.delete()
#        messages.success(request, 'Material request has been deleted')
#        return redirect('dashboard-index')
#    
#    context = {
#        'item': material_request,
#    }
#    return render(request, 'delete_item.html', context)
#
#@login_required(login_url='user-login')
#def update_work_site_report(request, pk):
#    work_site_report = get_object_or_404(WorkSiteReport, pk=pk)
#    if request.method == 'POST':
#        form = WorkSiteReportForm(request.POST, instance=work_site_report)
#        if form.is_valid():
#            form.save()
#            messages.success(request, 'Work site report has been updated')
#            return redirect('dashboard-index')
#    else:
#        form = WorkSiteReportForm(instance=work_site_report)
#    
#    context = {
#        'form': form,
#        'item': work_site_report,
#    }
#    return render(request, 'update_item.html', context)
#
#@login_required(login_url='user-login')
#def delete_work_site_report(request, pk):
#    work_site_report = get_object_or_404(WorkSiteReport, pk=pk)
#    if request.method == 'POST':
#        work_site_report.delete()
#        messages.success(request, 'Work site report has been deleted')
#        return redirect('dashboard-index')
#    
#    context = {
#        'item': work_site_report,
#    }
#    return render(request, 'delete_item.html', context)
#@login_required(login_url='user-login')
#def update_customer(request, pk):
#    customer = get_object_or_404(Customer, pk=pk)
#    if request.method == 'POST':
#        form = CustomerForm(request.POST, instance=customer)
#        if form.is_valid():
#            form.save()
#            messages.success(request, f'Customer {customer.user.username} has been updated')
#            return redirect('dashboard-customers')
#    else:
#        form = CustomerForm(instance=customer)
#
#    context = {
#        'form': form,
#        'customer': customer,
#    }
#    return render(request, 'update_customer.html', context)
#
#@login_required(login_url='user-login')
#def delete_customer(request, pk):
#    customer = get_object_or_404(Customer, pk=pk)
#    if request.method == 'POST':
#        customer.user.delete()  # Delete associated User object
#        messages.success(request, f'Customer {customer.user.username} has been deleted')
#        return redirect('dashboard-customers')
#
#    context = {
#        'customer': customer,
#    }
#    return render(request, 'delete_customer.html', context)
