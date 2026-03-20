from django.test import TestCase, RequestFactory
from rest_framework.test import APIRequestFactory

from donations.models import Donation, ResearcherToken
from donations.authentication import ResearcherTokenAuthentication
from donations.utils.crypto import encrypt_text, decrypt_text, encrypt_bytes, decrypt_bytes


class CryptoTests(TestCase):
    def test_text_roundtrip(self):
        original = "secret oauth token value"
        encrypted = encrypt_text(original)
        self.assertNotEqual(encrypted, original)
        self.assertEqual(decrypt_text(encrypted), original)

    def test_bytes_roundtrip(self):
        original = b"binary data here"
        encrypted = encrypt_bytes(original)
        self.assertNotEqual(encrypted, original)
        self.assertEqual(decrypt_bytes(encrypted), original)

    def test_empty_string(self):
        self.assertEqual(decrypt_text(encrypt_text("")), "")


class DonationModelTests(TestCase):
    def test_create_donation(self):
        donation = Donation.objects.create(source_type='google_portability')
        self.assertEqual(donation.status, 'pending')
        self.assertIsNotNone(donation.participant_token)
        self.assertIsNotNone(donation.researcher_token)
        self.assertNotEqual(donation.participant_token, donation.researcher_token)

    def test_unique_tokens(self):
        d1 = Donation.objects.create(source_type='google_portability')
        d2 = Donation.objects.create(source_type='tiktok_portability')
        self.assertNotEqual(d1.participant_token, d2.participant_token)
        self.assertNotEqual(d1.researcher_token, d2.researcher_token)


class ResearcherTokenModelTests(TestCase):
    def test_auto_generates_key(self):
        token = ResearcherToken.objects.create(permission='add_user', name='test')
        self.assertEqual(len(token.key), 40)

    def test_permission_choices(self):
        t1 = ResearcherToken.objects.create(permission='add_user')
        t2 = ResearcherToken.objects.create(permission='read_data')
        self.assertEqual(t1.permission, 'add_user')
        self.assertEqual(t2.permission, 'read_data')


class ResearcherTokenAuthTests(TestCase):
    def setUp(self):
        self.auth = ResearcherTokenAuthentication()
        self.factory = APIRequestFactory()
        self.token = ResearcherToken.objects.create(
            permission='add_user', name='test-auth'
        )

    def test_valid_token(self):
        request = self.factory.get('/', HTTP_AUTHORIZATION=f'Token {self.token.key}')
        user, auth_token = self.auth.authenticate(request)
        self.assertIsNone(user)
        self.assertEqual(auth_token.permission, 'add_user')
        self.assertEqual(auth_token.key, self.token.key)

    def test_invalid_token(self):
        request = self.factory.get('/', HTTP_AUTHORIZATION='Token invalidkey123')
        from rest_framework.exceptions import AuthenticationFailed
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    def test_no_header(self):
        request = self.factory.get('/')
        result = self.auth.authenticate(request)
        self.assertIsNone(result)
