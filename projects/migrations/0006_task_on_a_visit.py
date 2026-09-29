"""
A task can belong to a visit, not only to a project.

Work given to a technician was attached to a project and nowhere nearer, so
somebody standing on a site could see "three things to do on this project"
and nothing saying which of them belonged to the visit they were on. A task
now points at the field job it is part of, where it is part of one.

`project` becomes nullable to go with it: a visit can be scheduled straight
off a ticket with no project behind it, and its checklist still has to live
somewhere. Nothing existing changes — every task already has a project, and
`Task.save` keeps filling it in from the visit wherever the visit has one.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fieldjobs', '0002_fieldjobcrew'),
        ('projects', '0005_task_date_range'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='field_job',
            field=models.ForeignKey(blank=True, help_text='The visit this is part of, where it is part of one.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='fieldjobs.fieldjob'),
        ),
        migrations.AlterField(
            model_name='task',
            name='project',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='projects.project'),
        ),
    ]
