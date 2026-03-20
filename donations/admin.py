"""Admin configuration for donation models."""
from django.contrib import admin

from donations.models import Donation, GoogleDonation, ResearcherToken


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    """Admin interface for managing donations."""
    list_display = ('id', 'source_type', 'status', 'created_at')
    list_filter = ('status', 'source_type')
    readonly_fields = ('participant_token', 'researcher_token', 'created_at')


@admin.register(ResearcherToken)
class ResearcherTokenAdmin(admin.ModelAdmin):
    """Admin interface for managing researcher tokens."""
    list_display = ('name', 'permission', 'key', 'created_at')
    list_filter = ('permission',)
    readonly_fields = ('created_at',)


@admin.register(GoogleDonation)
class GoogleDonationAdmin(admin.ModelAdmin):
    """Admin interface for managing Google donations."""
    list_display = ('id', 'status', 'processing_status', 'created_at')
    list_filter = ('status', 'processing_status')
    readonly_fields = ('participant_token', 'researcher_token', 'created_at')
