import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portability_server.settings')

app = Celery('portability_server')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
