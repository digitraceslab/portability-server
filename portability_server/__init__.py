"""Portability server Django project initialization."""
from portability_server.celery import app as celery_app

__all__ = ('celery_app',)
