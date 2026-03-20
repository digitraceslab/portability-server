"""URL configuration for portability-server."""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path


def health_check(request):
    """Health check endpoint returning ok status."""
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health_check, name='health-check'),
]
