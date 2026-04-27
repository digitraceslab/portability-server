"""Admin configuration for donation models."""
from django.contrib import admin, messages

from donations.models import Donation, GoogleDonation, TikTokDonation, ResearcherToken, Participant


def _regenerate_one(modeladmin, request, queryset, kind):
    """Shared helper for regenerate-token admin actions."""
    if queryset.count() != 1:
        modeladmin.message_user(
            request, "Please select exactly one row to regenerate.", messages.WARNING)
        return
    obj = queryset.first()
    raw_token = obj.regenerate_token()
    modeladmin.message_user(
        request,
        f"New {kind} token for #{obj.pk}: {raw_token} — Save this now, it will not be shown again.",
        messages.SUCCESS,
    )


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    """Admin interface for managing participants."""
    list_display = ('id', 'created_at')
    readonly_fields = ('token', 'created_at')
    actions = ['regenerate_token']

    @admin.action(description="Regenerate selected participant token (select only one)")
    def regenerate_token(self, request, queryset):
        _regenerate_one(self, request, queryset, 'participant')


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    """Admin interface for managing donations."""
    list_display = ('id', 'source_type', 'researcher', 'participant', 'status', 'created_at')
    list_filter = ('status', 'source_type')
    readonly_fields = ('token', 'created_at')
    actions = ['regenerate_token']

    @admin.action(description="Regenerate selected donation token (select only one)")
    def regenerate_token(self, request, queryset):
        _regenerate_one(self, request, queryset, 'donation')


@admin.register(ResearcherToken)
class ResearcherTokenAdmin(admin.ModelAdmin):
    """Admin interface for managing researcher tokens."""
    list_display = ('name', 'created_at')
    readonly_fields = ('key', 'created_at')
    actions = ['regenerate_token']

    @admin.action(description="Regenerate selected token (select only one)")
    def regenerate_token(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Please select exactly one token to regenerate.", messages.WARNING)
            return
        token_obj = queryset.first()
        raw_key = token_obj.regenerate_key()
        self.message_user(
            request,
            f"New token for '{token_obj.name or token_obj.pk}': {raw_key} — Save this now, it will not be shown again.",
            messages.SUCCESS,
        )


@admin.register(GoogleDonation)
class GoogleDonationAdmin(admin.ModelAdmin):
    """Admin interface for managing Google donations."""
    list_display = ('id', 'researcher', 'status', 'processing_status', 'created_at')
    list_filter = ('status', 'processing_status')
    readonly_fields = ('token', 'created_at')
    actions = ['regenerate_token']

    @admin.action(description="Regenerate selected donation token (select only one)")
    def regenerate_token(self, request, queryset):
        _regenerate_one(self, request, queryset, 'donation')


@admin.register(TikTokDonation)
class TikTokDonationAdmin(admin.ModelAdmin):
    """Admin interface for managing TikTok donations."""
    list_display = ('id', 'researcher', 'status', 'processing_status', 'created_at')
    list_filter = ('status', 'processing_status')
    readonly_fields = ('token', 'created_at')
    actions = ['regenerate_token']

    @admin.action(description="Regenerate selected donation token (select only one)")
    def regenerate_token(self, request, queryset):
        _regenerate_one(self, request, queryset, 'donation')
