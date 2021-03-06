# Generated by Django 3.2.4 on 2021-06-15 16:01

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('manager', '0015_auto_20210601_0340'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('providers', '0014_auto_20210612_1608'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='PurolatorCourierSettings',
            new_name='PurolatorSettings',
        ),
        migrations.AlterModelOptions(
            name='purolatorsettings',
            options={'verbose_name': 'Purolator Settings', 'verbose_name_plural': 'Purolator Settings'},
        ),
        migrations.AlterModelTable(
            name='purolatorsettings',
            table='purolator-settings',
        ),
    ]
