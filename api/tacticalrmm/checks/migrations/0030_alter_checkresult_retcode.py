# Generated by Django 4.0.5 on 2022-06-29 07:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('checks', '0029_alter_checkresult_alert_severity'),
    ]

    operations = [
        migrations.AlterField(
            model_name='checkresult',
            name='retcode',
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
