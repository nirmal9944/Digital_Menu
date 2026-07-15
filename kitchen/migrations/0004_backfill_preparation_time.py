from django.db import migrations


def backfill_preparation_time(apps, schema_editor):
    KitchenTicketItem = apps.get_model('kitchen', 'KitchenTicketItem')
    for ti in KitchenTicketItem.objects.select_related('order_item__food'):
        try:
            ti.preparation_time = ti.order_item.food.preparation_time
        except Exception:
            continue
        ti.save(update_fields=['preparation_time'])


class Migration(migrations.Migration):

    dependencies = [
        ('kitchen', '0003_kitchenticketitem_preparation_time'),
    ]

    operations = [
        migrations.RunPython(backfill_preparation_time, migrations.RunPython.noop),
    ]
