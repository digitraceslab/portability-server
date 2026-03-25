"""Hash existing plaintext ResearcherToken keys with SHA-256.

WARNING: After this migration, existing plaintext tokens are replaced with
their SHA-256 hashes. The original token values cannot be recovered.
All existing researcher tokens will need to be re-issued.
"""
import hashlib

from django.db import migrations, models


def hash_existing_keys(apps, schema_editor):
    ResearcherToken = apps.get_model('donations', 'ResearcherToken')
    for token in ResearcherToken.objects.all():
        # Only hash if it looks like a plaintext key (not already 64-char hex)
        if len(token.key) != 64:
            token.key = hashlib.sha256(token.key.encode()).hexdigest()
            token.save(update_fields=['key'])


class Migration(migrations.Migration):

    dependencies = [
        ('donations', '0010_donation_requested_data_types'),
    ]

    operations = [
        migrations.AlterField(
            model_name='researchertoken',
            name='key',
            field=models.CharField(max_length=64, unique=True),
        ),
        migrations.RunPython(hash_existing_keys, migrations.RunPython.noop),
    ]
