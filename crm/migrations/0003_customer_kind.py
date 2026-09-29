"""
A customer is a person or an organisation, and the register now says which.

Every existing row becomes an organisation, which is what A-1's work mostly
is and what the column defaults to. The named few below were people on the
list when the type was introduced, and are set as such so nobody has to
re-read the register looking for them.

Names, because that is all there was to go on. A name not on the list is
left alone, so this is safe on a database that never held these rows.
"""
from django.db import migrations, models

WERE_PEOPLE = ["Faith Jallah"]


def mark_the_people(apps, schema_editor):
    Customer = apps.get_model("crm", "Customer")
    Customer.objects.filter(name__in=WERE_PEOPLE).update(kind="individual")


def back_to_organisations(apps, schema_editor):
    """The column is about to be dropped; this only keeps the reverse total."""
    Customer = apps.get_model("crm", "Customer")
    Customer.objects.filter(name__in=WERE_PEOPLE).update(kind="organisation")


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0002_main_sites_from_addresses'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='kind',
            field=models.CharField(choices=[('individual', 'Individual'), ('organisation', 'Organisation')], default='organisation', max_length=20),
        ),
        migrations.RunPython(mark_the_people, back_to_organisations),
    ]
