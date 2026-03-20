from django.core.management.base import BaseCommand

from donations.models import ResearcherToken


class Command(BaseCommand):
    help = 'Create a researcher API token'

    def add_arguments(self, parser):
        parser.add_argument(
            '--name',
            type=str,
            default='',
            help='Descriptive label for this token',
        )

    def handle(self, *args, **options):
        token = ResearcherToken.objects.create(
            name=options['name'],
        )
        self.stdout.write(f"Created token: {token.key}")
        if token.name:
            self.stdout.write(f"  Name: {token.name}")
