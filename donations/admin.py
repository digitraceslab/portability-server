from django.contrib import admin

from donations.models import Donation, ResearcherToken


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ('id', 'source_type', 'status', 'created_at')
    list_filter = ('status', 'source_type')
    readonly_fields = ('participant_token', 'researcher_token', 'created_at')


@admin.register(ResearcherToken)
class ResearcherTokenAdmin(admin.ModelAdmin):
    list_display = ('name', 'permission', 'key', 'created_at')
    list_filter = ('permission',)
    readonly_fields = ('created_at',)
