"""
A task now runs over a range: the day it can start, and the day it is due.

Nothing is backfilled. A task recorded before this has no start date, and a
range with one end reads as it always did — "due 12 Sept" — so no existing
row is given a start somebody never chose.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0004_requisition_items'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='start_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]
