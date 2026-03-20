"""Admin configuration for donation models."""
from django.contrib import admin

from donations.models import Donation, GoogleDonation, TikTokDonation, ResearcherToken


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    """Admin interface for managing donations."""
    list_display = ('id', 'source_type', 'researcher', 'status', 'created_at')
    list_filter = ('status', 'source_type')
    readonly_fields = ('participant_token', 'created_at')


@admin.register(ResearcherToken)
class ResearcherTokenAdmin(admin.ModelAdmin):
    """Admin interface for managing researcher tokens."""
    list_display = ('name', 'key', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(GoogleDonation)
class GoogleDonationAdmin(admin.ModelAdmin):
    """Admin interface for managing Google donations."""
    list_display = ('id', 'participant_token', 'researcher', 'status', 'processing_status', 'created_at')
    list_filter = ('status', 'processing_status')
    readonly_fields = ('participant_token', 'created_at')


@admin.register(TikTokDonation)
class TikTokDonationAdmin(admin.ModelAdmin):
    """Admin interface for managing TikTok donations."""
    list_display = ('id', 'participant_token', 'researcher', 'status', 'processing_status', 'created_at')
    list_filter = ('status', 'processing_status')
    readonly_fields = ('participant_token', 'created_at')
