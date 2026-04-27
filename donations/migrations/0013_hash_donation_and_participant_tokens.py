"""Hash existing plaintext Donation and Participant tokens with SHA-256.

WARNING: After this migration, existing plaintext UUID tokens are replaced with
their SHA-256 hashes. The original UUID values cannot be recovered from the
database, so any URLs already issued (e.g. ``/donate/<uuid>/``,
``/participant/<uuid>/``) will continue to work for the participants that hold
them, but the database alone cannot reproduce them. Use the admin
"regenerate token" action to issue a new token if a participant loses theirs.
"""
import hashlib

from django.db import migrations, models


def _hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def hash_existing_tokens(apps, schema_editor):
    Donation = apps.get_model('donations', 'Donation')
    Participant = apps.get_model('donations', 'Participant')
    for donation in Donation.objects.all():
        if len(donation.token) != 64:
            donation.token = _hash(donation.token)
            donation.save(update_fields=['token'])
    for participant in Participant.objects.all():
        if len(participant.token) != 64:
            participant.token = _hash(participant.token)
            participant.save(update_fields=['token'])


class Migration(migrations.Migration):

    dependencies = [
        ('donations', '0012_donation_suggested_participant_token'),
    ]

    operations = [
        migrations.AlterField(
            model_name='donation',
            name='token',
            field=models.CharField(editable=False, max_length=64, unique=True),
        ),
        migrations.AlterField(
            model_name='participant',
            name='token',
            field=models.CharField(editable=False, max_length=64, unique=True),
        ),
        migrations.RunPython(hash_existing_tokens, migrations.RunPython.noop),
    ]
