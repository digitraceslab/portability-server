"""Admin configuration for donation models.
"""
from django.contrib import admin

from donations.models import Donation, GoogleDonation, TikTokDonation, ResearcherToken, ResearcherSession, Participant
from donations.models.tiktok_export import TikTokExportDonation


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    """Admin interface for managing participants."""
    list_display = ('id', 'created_at')
    readonly_fields = ('token', 'created_at')


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    """Admin interface for managing donations."""
    list_display = ('id', 'source_type', 'researcher', 'participant', 'status', 'created_at')
    list_filter = ('status', 'source_type')
    readonly_fields = ('token', 'created_at')


@admin.register(ResearcherToken)
class ResearcherTokenAdmin(admin.ModelAdmin):
    """Admin interface for managing researcher tokens."""
    list_display = ('name', 'created_at', 'expires_at')
    readonly_fields = ('key', 'created_at')


@admin.register(ResearcherSession)
class ResearcherSessionAdmin(admin.ModelAdmin):
    """Read-only admin interface for inspecting and killing researcher sessions."""
    list_display = ('token', 'created_at', 'expires_at')
    readonly_fields = ('token', 'created_at', 'expires_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(GoogleDonation)
class GoogleDonationAdmin(admin.ModelAdmin):
    """Admin interface for managing Google donations."""
    list_display = ('id', 'researcher', 'status', 'processing_status', 'created_at')
    list_filter = ('status', 'processing_status')
    readonly_fields = ('token', 'created_at')


@admin.register(TikTokDonation)
class TikTokDonationAdmin(admin.ModelAdmin):
    """Admin interface for managing TikTok donations."""
    list_display = ('id', 'researcher', 'status', 'processing_status', 'created_at')
    list_filter = ('status', 'processing_status')
    readonly_fields = ('token', 'created_at')


@admin.register(TikTokExportDonation)
class TikTokExportDonationAdmin(admin.ModelAdmin):
    """Admin interface for managing TikTok Export donations."""
    list_display = ('id', 'researcher', 'status', 'processing_status', 'created_at')
    list_filter = ('status', 'processing_status')
    readonly_fields = ('token', 'created_at')
