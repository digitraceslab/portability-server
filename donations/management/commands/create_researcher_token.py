"""Management command to create a researcher API token."""
from datetime import datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date

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
        parser.add_argument(
            '--expires',
            type=str,
            required=True,
            help='Date the token stops working, YYYY-MM-DD (end of that day).',
        )

    def handle(self, *args, **options):
        expires_date = parse_date(options['expires'])
        if expires_date is None:
            raise CommandError("--expires must be a date in YYYY-MM-DD format.")
        expires_at = timezone.make_aware(datetime.combine(expires_date, time.max))

        token = ResearcherToken.objects.create(
            name=options['name'],
            expires_at=expires_at,
        )
        self.stdout.write(f"Created token: {token._raw_key}")
        self.stdout.write("Save this token now — it will not be shown again.")
        if token.name:
            self.stdout.write(f"  Name: {token.name}")
        self.stdout.write(f"  Expires: {token.expires_at:%Y-%m-%d %H:%M %Z}")
