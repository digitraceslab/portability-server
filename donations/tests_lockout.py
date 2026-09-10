"""Tests for django-axes account lockout on the admin login."""
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from axes.utils import reset

LOGIN_URL = "/admin/login/"
CLIENT_IP = "203.0.113.1"


class AdminLoginLockoutTests(TestCase):
    """A username is locked out after AXES_FAILURE_LIMIT failed logins."""

    def setUp(self):
        self.username = "operator"
        self.password = "correct horse battery staple 12"
        self.user = get_user_model().objects.create_superuser(
            username=self.username,
            email="operator@example.com",
            password=self.password,
        )
        self.client = Client(HTTP_X_REAL_IP=CLIENT_IP)

    def _login(self, username, password):
        return self.client.post(
            f"{LOGIN_URL}?next={reverse('admin:index')}",
            {"username": username, "password": password, "next": reverse("admin:index")},
            HTTP_X_REAL_IP=CLIENT_IP,
        )

    def test_account_locks_after_failure_limit(self):
        for _ in range(4):
            response = self._login(self.username, "wrong password")
            self.assertEqual(response.status_code, 200)

        # The failure that reaches AXES_FAILURE_LIMIT is itself locked out;
        # axes' default lockout response is 429 Too Many Requests.
        response = self._login(self.username, "wrong password")
        self.assertEqual(response.status_code, 429)

        response = self._login(self.username, self.password)
        self.assertEqual(response.status_code, 429)

    def test_reset_clears_lockout(self):
        for _ in range(5):
            self._login(self.username, "wrong password")

        response = self._login(self.username, self.password)
        self.assertEqual(response.status_code, 429)

        reset(username=self.username)

        response = self._login(self.username, self.password)
        self.assertRedirects(response, reverse("admin:index"), fetch_redirect_response=False)

    def test_lockout_is_per_account(self):
        other_username = "other-operator"
        other_password = "another correct horse battery 34"
        get_user_model().objects.create_superuser(
            username=other_username,
            email="other@example.com",
            password=other_password,
        )

        for _ in range(5):
            self._login(self.username, "wrong password")

        response = self._login(self.username, self.password)
        self.assertEqual(response.status_code, 429)

        response = self._login(other_username, other_password)
        self.assertRedirects(response, reverse("admin:index"), fetch_redirect_response=False)
