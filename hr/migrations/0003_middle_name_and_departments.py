"""
Departments become a list instead of a text box.

"Operations", "operations" and "Ops" were three departments as far as any
report was concerned, and nobody found out until a roll-call was grouped by
one. The client asked for a dropdown; this is the list behind it.

Done in four steps rather than one AlterField, because changing a text
column into a foreign key in a single step throws the text away: the names
already typed are exactly what the list should start as. The old column is
kept until its contents have been read across, then dropped.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def text_into_list(apps, schema_editor):
    """Every department anybody has typed becomes a row, matched case- and
    space-insensitively so three spellings of one department converge."""
    Department = apps.get_model("hr", "Department")
    Employee = apps.get_model("hr", "Employee")

    seen = {}
    for employee in Employee.objects.exclude(department_text="").order_by("pk"):
        raw = " ".join(employee.department_text.split())
        if not raw:
            continue
        key = raw.lower()
        if key not in seen:
            # The first spelling encountered is the one kept.
            seen[key] = Department.objects.create(name=raw, order=len(seen))
        employee.department = seen[key]
        employee.save(update_fields=["department"])


def list_back_into_text(apps, schema_editor):
    Employee = apps.get_model("hr", "Employee")
    for employee in Employee.objects.exclude(department__isnull=True).select_related("department"):
        employee.department_text = employee.department.name
        employee.save(update_fields=["department_text"])


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0002_attendancecode_attendanceevent_attendance_code_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Department',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_active', models.BooleanField(default=True)),
                ('deactivated_at', models.DateTimeField(blank=True, null=True)),
                ('name', models.CharField(max_length=80, unique=True)),
                ('order', models.PositiveIntegerField(default=0)),
                ('deactivated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['order', 'name'],
            },
        ),
        # Keep what was typed, under a name the model no longer uses.
        migrations.RenameField(
            model_name='employee',
            old_name='department',
            new_name='department_text',
        ),
        migrations.AddField(
            model_name='employee',
            name='department',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='employees', to='hr.department'),
        ),
        migrations.RunPython(text_into_list, list_back_into_text),
        migrations.RemoveField(
            model_name='employee',
            name='department_text',
        ),
    ]
