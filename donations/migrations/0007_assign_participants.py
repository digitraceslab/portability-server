from django.db import migrations


def assign_participants(apps, schema_editor):
    Participant = apps.get_model('donations', 'Participant')
    Donation = apps.get_model('donations', 'Donation')
    for donation in Donation.objects.filter(participant__isnull=True):
        participant = Participant.objects.create()
        donation.participant = participant
        donation.save(update_fields=['participant'])


def reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('donations', '0006_participant_donation_participant'),
    ]
    operations = [
        migrations.RunPython(assign_participants, reverse),
    ]
