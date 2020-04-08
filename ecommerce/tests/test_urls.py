from __future__ import absolute_import

from django.urls import reverse

from ecommerce.core.url_utils import get_lms_dashboard_url
from ecommerce.tests.testcases import TestCase


class TestUrls(TestCase):
    def assert_redirect_to_lms(self, url):
        response = self.client.get(url)
        # Test client can't fetch external URLs, so fetch_redirect_response is set to
        # False to avoid loading the final page
        self.assertRedirects(response, get_lms_dashboard_url(), fetch_redirect_response=False)

    def test_unauthorized_redirection(self):
        """Test that users not authorized to access the Oscar front-end are redirected to the LMS dashboard."""
        user = self.create_user()

        # Log in as a user not authorized to view the Oscar front-end (no staff permissions)
        success = self.client.login(username=user.username, password=self.password)
        self.assertTrue(success)
        self.assert_redirect_to_lms(reverse('dashboard:index'))

    def test_api_docs(self):
        """
        Verify that the API docs render.
        """
        path = reverse('api_docs')
        response = self.client.get(path)

        assert response.status_code == 200

    def test_unauthorized_homepage_redirection(self):
        """Test that users unauthorized to access the Oscar front-end are redirected to LMS Dashboard."""
        user = self.create_user()
        # Log in as a user not authorized to view the Oscar Dashboard (no staff permissions)
        success = self.client.login(username=user.username, password=self.password)
        self.assertTrue(success)
        response = self.client.get('/')
        self.assertRedirects(response, reverse('dashboard:index'), target_status_code=302)
        self.assert_redirect_to_lms(response.url)

    def test_authorized_homepage_redirection(self):
        """Test that users authorized to access the Oscar front-end are redirected to Oscar Dashboard."""
        user = self.create_user(is_staff=True)
        # Log in as a user authorized to view the Oscar Dashboard (staff permissions)
        success = self.client.login(username=user.username, password=self.password)
        self.assertTrue(success)
        response = self.client.get('/')
        self.assertRedirects(response, reverse('dashboard:index'))
