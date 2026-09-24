"""Management command to regenerate an authentication token."""
from django.core.management.base import BaseCommand, CommandError

from donations.models import Donation, Participant, ResearcherToken


class Command(BaseCommand):
    help = (
        'Regenerate a token, invalidating the old one; the new raw value is '
        'printed once and cannot be recovered later. Mostly useful for '
        'researcher tokens: a regenerated donation or participant token is a '
        'new entry link that must be handed to the participant, who has no '
        'other way to reach their donation.'
    )

    MODELS = {
        'researcher': ResearcherToken,
        'donation': Donation,
        'participant': Participant,
    }

    def add_arguments(self, parser):
        parser.add_argument(
            'model',
            choices=sorted(self.MODELS),
            help='Which kind of token to regenerate',
        )
        parser.add_argument('pk', type=int, help='Primary key of the row')

    def handle(self, *args, **options):
        model = self.MODELS[options['model']]
        try:
            obj = model.objects.get(pk=options['pk'])
        except model.DoesNotExist:
            raise CommandError(f"No {options['model']} with pk {options['pk']}.")
        if isinstance(obj, ResearcherToken):
            raw = obj.regenerate_key()
            note = 'Existing researcher sessions have been deleted.'
        else:
            raw = obj.regenerate_token()
            note = 'The old entry link no longer works.'
        self.stdout.write(f"New {options['model']} token for #{obj.pk}: {raw}")
        self.stdout.write('Save this token now — it will not be shown again.')
        self.stdout.write(note)
