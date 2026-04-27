"""URL configuration for portability-server."""
from django.contrib import admin
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import include, path

from rest_framework.routers import DefaultRouter

from donations import views
from donations.api import DonationViewSet, api_docs

router = DefaultRouter()
router.register(r'donations', DonationViewSet, basename='api-donation')


def health_check(request):
    """Health check endpoint returning ok status."""
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    path('', lambda r: render(r, 'home.html'), name='home'),
    path('admin/', admin.site.urls),
    path('health/', health_check, name='health-check'),
    path('terms/', lambda r: render(r, 'donations/terms_of_service.html'), name='terms-of-service'),
    path('privacy/', lambda r: render(r, 'donations/privacy_notice.html'), name='privacy-notice'),
    # Token entry points: consume token from URL, store in session, redirect to clean URL.
    path('donate/<uuid:donation_token>/', views.donation_entry, name='donation-entry'),
    path('participant/<uuid:token>/', views.participant_entry, name='participant-entry'),
    # Tokenless participant-facing views (auth via session).
    path('donate/', views.donation_landing, name='donation-landing'),
    path('donate/terms/', views.accept_terms, name='accept-terms'),
    path('donate/authorize/', views.authorize, name='authorize'),
    path('donate/data/', views.data_preview, name='data-preview'),
    path('donate/revoke/', views.revoke_donation, name='revoke-donation'),
    path('donate/switch-to-participant/', views.switch_to_participant, name='switch-to-participant'),
    path('donate/generate-participant/', views.generate_participant_token, name='generate-participant'),
    path('participant/logout/', views.logout_participant, name='logout-participant'),
    path('participant/', views.participant_home, name='participant-home'),
    path('participant/select/<int:donation_pk>/', views.select_donation, name='select-donation'),
    # OAuth callbacks
    path('oauth/google/callback/', views.google_auth_callback, name='google-auth-callback'),
    path('oauth/tiktok/callback/', views.tiktok_auth_callback, name='tiktok-auth-callback'),
    # API
    path('api/docs/', api_docs, name='api-docs'),
    path('api/', include(router.urls)),
]
