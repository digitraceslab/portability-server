"""URL configuration for portability-server."""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from rest_framework.routers import DefaultRouter

from donations import views
from donations.api import DonationViewSet

router = DefaultRouter()
router.register(r'donations', DonationViewSet, basename='api-donation')


def health_check(request):
    """Health check endpoint returning ok status."""
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health_check, name='health-check'),
    # Participant-facing views
    path('donate/<uuid:donation_token>/', views.donation_landing, name='donation-landing'),
    path('donate/<uuid:donation_token>/terms/', views.accept_terms, name='accept-terms'),
    path('donate/<uuid:donation_token>/authorize/', views.authorize, name='authorize'),
    path('donate/<uuid:donation_token>/data/', views.data_preview, name='data-preview'),
    path('donate/<uuid:donation_token>/revoke/', views.revoke_donation, name='revoke-donation'),
    # OAuth callbacks
    path('oauth/google/callback/', views.google_auth_callback, name='google-auth-callback'),
    path('oauth/tiktok/callback/', views.tiktok_auth_callback, name='tiktok-auth-callback'),
    # Participant views
    path('participant/<uuid:token>/', views.participant_home, name='participant-home'),
    # API
    path('api/', include(router.urls)),
]
