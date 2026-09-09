"""Tests for researcher token/session library and the /api/session/ endpoint."""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from donations.models import ResearcherSession, ResearcherToken
from donations.researcher_auth.sessions import (
    create_session, purge_expired_sessions, resolve_session, revoke_session,
)
from donations.researcher_auth.tokens import resolve_researcher_token


def _make_token(name='test', expires_in_days=30):
    return ResearcherToken.objects.create(
        name=name, expires_at=timezone.now() + timedelta(days=expires_in_days),
    )


class ResolveResearcherTokenTests(TestCase):
    def test_valid_token_resolves(self):
        token = _make_token()
        self.assertEqual(resolve_researcher_token(token._raw_key).pk, token.pk)

    def test_unknown_token_returns_none(self):
        self.assertIsNone(resolve_researcher_token('does-not-exist'))

    def test_expired_token_returns_none(self):
        token = _make_token(expires_in_days=-1)
        self.assertIsNone(resolve_researcher_token(token._raw_key))


class CreateSessionTests(TestCase):
    def test_returns_raw_key_and_session(self):
        token = _make_token()
        raw_key, session = create_session(token)
        self.assertEqual(len(raw_key), 40)
        self.assertEqual(session.token, token)
        self.assertEqual(session.key, ResearcherSession.hash_key(raw_key))

    @override_settings(RESEARCHER_SESSION_LIFETIME_SECONDS=3600)
    def test_expiry_uses_the_lifetime_setting(self):
        token = _make_token(expires_in_days=30)
        before = timezone.now()
        _, session = create_session(token)
        self.assertAlmostEqual(
            (session.expires_at - before).total_seconds(), 3600, delta=5,
        )

    @override_settings(RESEARCHER_SESSION_LIFETIME_SECONDS=999999999)
    def test_expiry_is_capped_at_the_token_expiry(self):
        token = _make_token(expires_in_days=1)
        _, session = create_session(token)
        self.assertEqual(session.expires_at, token.expires_at)


class ResolveSessionTests(TestCase):
    def setUp(self):
        self.token = _make_token()
        self.raw_key, self.session = create_session(self.token)

    def test_valid_session_resolves(self):
        self.assertEqual(resolve_session(self.raw_key).pk, self.session.pk)

    def test_unknown_session_returns_none(self):
        self.assertIsNone(resolve_session('does-not-exist'))

    def test_expired_session_returns_none(self):
        self.session.expires_at = timezone.now() - timedelta(seconds=1)
        self.session.save()
        self.assertIsNone(resolve_session(self.raw_key))


class RevokeSessionTests(TestCase):
    def test_deletes_the_session(self):
        token = _make_token()
        _, session = create_session(token)
        revoke_session(session)
        self.assertFalse(ResearcherSession.objects.filter(pk=session.pk).exists())


class PurgeExpiredSessionsTests(TestCase):
    def test_removes_only_expired_sessions_and_returns_the_count(self):
        token = _make_token()
        _, live = create_session(token)
        _, dead = create_session(token)
        dead.expires_at = timezone.now() - timedelta(seconds=1)
        dead.save()

        removed = purge_expired_sessions()

        self.assertEqual(removed, 1)
        self.assertTrue(ResearcherSession.objects.filter(pk=live.pk).exists())
        self.assertFalse(ResearcherSession.objects.filter(pk=dead.pk).exists())


class RegenerateKeyDeletesSessionsTests(TestCase):
    def test_regenerate_key_deletes_existing_sessions(self):
        token = _make_token()
        create_session(token)
        create_session(token)
        token.regenerate_key()
        self.assertEqual(ResearcherSession.objects.filter(token=token).count(), 0)


class CreateResearcherTokenCommandTests(TestCase):
    def test_expires_is_required(self):
        with self.assertRaises(CommandError):
            call_command('create_researcher_token', name='no-expiry', stdout=StringIO())

    def test_rejects_a_malformed_date(self):
        with self.assertRaises(CommandError):
            call_command('create_researcher_token', expires='not-a-date', stdout=StringIO())

    def test_sets_expiry_to_end_of_the_given_day(self):
        call_command('create_researcher_token', name='dated', expires='2030-06-15', stdout=StringIO())
        token = ResearcherToken.objects.get(name='dated')
        self.assertEqual(token.expires_at.date().isoformat(), '2030-06-15')
        self.assertEqual(token.expires_at.time().hour, 23)


class SessionEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token = _make_token()

    def test_login_returns_a_session_token(self):
        response = self.client.post('/api/session/', {'token': self.token._raw_key})
        self.assertEqual(response.status_code, 200)
        self.assertIn('session_token', response.data)
        self.assertIn('expires_at', response.data)
        self.assertTrue(
            resolve_session(response.data['session_token']) is not None
        )

    def test_login_missing_token_field(self):
        response = self.client.post('/api/session/', {})
        self.assertEqual(response.status_code, 400)

    def test_login_unknown_token(self):
        response = self.client.post('/api/session/', {'token': 'does-not-exist'})
        self.assertEqual(response.status_code, 401)

    def test_login_expired_token(self):
        expired = _make_token(expires_in_days=-1)
        response = self.client.post('/api/session/', {'token': expired._raw_key})
        self.assertEqual(response.status_code, 401)

    def test_logout_then_session_is_rejected(self):
        login = self.client.post('/api/session/', {'token': self.token._raw_key})
        session_token = login.data['session_token']

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {session_token}')
        logout = client.delete('/api/session/')
        self.assertEqual(logout.status_code, 204)

        response = client.get('/api/donations/')
        self.assertIn(response.status_code, [401, 403])

    def test_logout_without_a_session_header(self):
        response = self.client.delete('/api/session/')
        self.assertEqual(response.status_code, 401)
