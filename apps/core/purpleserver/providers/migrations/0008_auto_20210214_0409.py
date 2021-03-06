# Generated by Django 3.1.6 on 2021-02-14 04:09

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('providers', '0007_auto_20210213_0206'),
    ]

    operations = [
        migrations.AlterField(
            model_name='carrier',
            name='carrier_id',
            field=models.CharField(help_text='eg. canadapost, dhl_express, fedex, purolator_courrier, ups...', max_length=200, unique=True),
        ),
        migrations.AlterField(
            model_name='carrier',
            name='user',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterUniqueTogether(
            name='carrier',
            unique_together=set(),
        ),
    ]
