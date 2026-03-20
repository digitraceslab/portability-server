from django.core.management.base import BaseCommand

from donations.models import ResearcherToken


class Command(BaseCommand):
    help = 'Create a researcher API token with a given permission level'

    def add_arguments(self, parser):
        parser.add_argument(
            '--permission',
            type=str,
            required=True,
            choices=[c[0] for c in ResearcherToken.PERMISSION_CHOICES],
            help='Permission level: add_user or read_data',
        )
        parser.add_argument(
            '--name',
            type=str,
            default='',
            help='Descriptive label for this token',
        )

    def handle(self, *args, **options):
        token = ResearcherToken.objects.create(
            permission=options['permission'],
            name=options['name'],
        )
        self.stdout.write(f"Created token: {token.key}")
        self.stdout.write(f"  Permission: {token.permission}")
        if token.name:
            self.stdout.write(f"  Name: {token.name}")
