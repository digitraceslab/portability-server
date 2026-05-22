from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('donations', '0014_tiktokdonation_user_info'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tiktokdonation',
            name='tiktok_user_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]