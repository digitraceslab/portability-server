# Generated migration to add user_info field to TikTokDonation

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('donations', '0013_hash_donation_and_participant_tokens'),
    ]

    operations = [
        migrations.AddField(
            model_name='tiktokdonation',
            name='user_info',
            field=models.JSONField(blank=True, default=dict, help_text='User info from TikTok API (display_name, user_name, etc.)'),
        ),
    ]
