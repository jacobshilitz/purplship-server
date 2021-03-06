# Generated by Django 3.2.4 on 2021-06-29 16:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('manager', '0016_shipment_archived'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='shipment',
            name='archived',
        ),
        migrations.AlterField(
            model_name='shipment',
            name='status',
            field=models.CharField(choices=[('created', 'created'), ('purchased', 'purchased'), ('cancelled', 'cancelled'), ('shipped', 'shipped'), ('transit', 'transit'), ('delivered', 'delivered')], default='created', max_length=50),
        ),
    ]
