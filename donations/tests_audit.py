"""Tests for the data-access audit trail."""
from django.test import TestCase, RequestFactory

from donations.audit import audit, client_address
from donations.models import GoogleDonation, ResearcherToken
from donations.researcher_auth.sessions import create_session


class AuditHelperTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_client_address_prefers_x_real_ip(self):
        request = self.factory.get('/', HTTP_X_REAL_IP='10.1.2.3', REMOTE_ADDR='127.0.0.1')
        self.assertEqual(client_address(request), '10.1.2.3')

    def test_audit_line_names_event_donation_actor_and_ip(self):
        researcher = ResearcherToken.objects.create(name='r')
        donation = GoogleDonation.objects.create(researcher=researcher)
        request = self.factory.get('/', HTTP_X_REAL_IP='10.1.2.3')
        with self.assertLogs('donations.audit', level='INFO') as captured:
            audit('data_read', request, donation, actor='researcher:7', data_type='discover')
        line = captured.output[0]
        for fragment in ('event=data_read', f'donation={donation.pk}', 'actor=researcher:7',
                         'ip=10.1.2.3', 'data_type=discover'):
            self.assertIn(fragment, line)


class AuditEndpointTests(TestCase):
    def setUp(self):
        self.researcher = ResearcherToken.objects.create(name='r')
        self.donation = GoogleDonation.objects.create(researcher=self.researcher)
        raw_key, _ = create_session(self.researcher)
        self.auth = {'HTTP_AUTHORIZATION': f'Token {raw_key}'}

    def test_api_data_read_is_audited_without_data(self):
        with self.assertLogs('donations.audit', level='INFO') as captured:
            self.client.get(f'/api/donations/{self.donation.pk}/data/', **self.auth)
        self.assertIn(f'event=data_read donation={self.donation.pk} actor=researcher:{self.researcher.pk}',
                      captured.output[0])

    def test_session_login_is_audited(self):
        with self.assertLogs('donations.audit', level='INFO') as captured:
            self.client.post('/api/session/', {'token': self.researcher._raw_key},
                             content_type='application/json')
        self.assertIn(f'event=session_login actor=researcher:{self.researcher.pk}', captured.output[0])
