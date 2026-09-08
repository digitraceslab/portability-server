"""Donations app configuration."""
from django.apps import AppConfig
from django.core.checks import Warning, register


class DonationsConfig(AppConfig):
    """Configuration for the donations application."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'donations'


@register()
def check_encryption_key(app_configs, **kwargs):
    from django.conf import settings
    warnings = []
    if not settings.DEBUG and not settings.OPENBAO_ADDR:
        warnings.append(Warning(
            'Encryption key is held locally by the application; no OpenBao '
            'vault is configured.',
            hint=(
                'Set OPENBAO_ADDR (and the AppRole credential files) so key '
                'material stays in the vault.'
            ),
            id='donations.W001',
        ))
    return warnings
