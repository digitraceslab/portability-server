"""Donations app configuration."""
from django.apps import AppConfig
from django.core.checks import Error, register


class DonationsConfig(AppConfig):
    """Configuration for the donations application."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'donations'


@register()
def check_encryption_key(app_configs, **kwargs):
    from django.conf import settings
    errors = []
    if not getattr(settings, 'ENCRYPTION_KEY', None):
        errors.append(Error(
            'ENCRYPTION_KEY is not set.',
            hint=(
                'Set ENCRYPTION_KEY in your .env file. Generate one with: '
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            ),
            id='donations.E001',
        ))
    return errors
