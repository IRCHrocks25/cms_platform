"""Tests for the dashboard page_import_siblings endpoint."""
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Page, Template, Tenant


@override_settings(
    TENANT_BASE_DOMAIN="localhost",
    ALLOWED_HOSTS=["localhost", "testserver", "*"],
)
class PageImportSiblingsTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="op", password="x", is_staff=True,
        )
        self.template = Template.objects.create(
            name="Home tpl", html_source="<html><body><h1>Hi</h1></body></html>",
        )
        self.tenant = Tenant.objects.create(
            name="Susan Rabby", subdomain="susan-rabby", template=self.template,
            owner=self.staff,
        )
        self.client = Client()
        self.url = reverse("dashboard:page_import_siblings", args=[self.tenant.pk])
        self.client.force_login(self.staff)

    def _patch_fetch(self, *return_values):
        """Each call to fetch_url_html returns the next value in sequence."""
        return mock.patch(
            "core.services.url_fetch.fetch_url_html",
            side_effect=list(return_values),
        )

    def test_requires_login(self):
        self.client.logout()
        r = self.client.post(self.url, data="{}", content_type="application/json")
        self.assertIn(r.status_code, (302, 403))

    def test_requires_home_url(self):
        r = self.client.post(self.url, data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("home_url", r.json()["error"])

    def test_propagates_home_fetch_error(self):
        with mock.patch(
            "core.services.url_fetch.fetch_url_html",
            side_effect=__import__(
                "core.services.url_fetch", fromlist=["UrlFetchError"],
            ).UrlFetchError("dead"),
        ):
            r = self.client.post(
                self.url,
                data='{"home_url": "https://busted.example.com/"}',
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("Could not fetch", r.json()["error"])

    def test_no_siblings_returns_empty(self):
        home_html = "<html><body><p>No links here</p></body></html>"
        with self._patch_fetch(home_html):
            r = self.client.post(
                self.url,
                data='{"home_url": "https://example.com/"}',
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["created"], [])
        self.assertEqual(body["skipped"], [])
        self.assertIn("No same-origin", body.get("message", ""))

    def test_creates_pages_for_each_sibling(self):
        # Patch the background thread to be a no-op so we don't actually
        # call OpenAI in tests.
        with mock.patch(
            "dashboard.views._annotate_template_in_background",
        ):
            home_html = (
                "<html><body>"
                "<a href='./privacy-policy.html'>Privacy</a>"
                "<a href='./terms-and-conditions.html'>Terms</a>"
                "</body></html>"
            )
            privacy_html = "<html><body>Privacy content</body></html>"
            terms_html = "<html><body>Terms content</body></html>"
            with self._patch_fetch(home_html, privacy_html, terms_html):
                r = self.client.post(
                    self.url,
                    data='{"home_url": "https://susan-rabbyv2.pages.dev/"}',
                    content_type="application/json",
                )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        slugs_created = sorted(c["slug"] for c in body["created"])
        self.assertEqual(slugs_created, ["privacy-policy", "terms-and-conditions"])
        self.assertEqual(
            sorted(self.tenant.pages.values_list("slug", flat=True)),
            ["privacy-policy", "terms-and-conditions"],
        )
        for c in body["created"]:
            self.assertEqual(c["annotation_status"], "pending")

    def test_skips_existing_slugs(self):
        existing_tpl = Template.objects.create(
            name="Existing privacy", html_source="<html></html>",
        )
        Page.objects.create(
            tenant=self.tenant, template=existing_tpl,
            title="Privacy", slug="privacy-policy",
        )
        with mock.patch("dashboard.views._annotate_template_in_background"):
            home_html = "<a href='./privacy-policy.html'>P</a>"
            with self._patch_fetch(home_html):
                r = self.client.post(
                    self.url,
                    data='{"home_url": "https://example.com/"}',
                    content_type="application/json",
                )
        body = r.json()
        self.assertEqual(body["created"], [])
        self.assertEqual(len(body["skipped"]), 1)
        self.assertEqual(body["skipped"][0]["slug"], "privacy-policy")
        self.assertIn("already exists", body["skipped"][0]["reason"])

    def test_kicks_off_background_annotation(self):
        """Sanity: the import path actually calls the background annotator
        helper for each successfully-imported sibling."""
        with mock.patch(
            "dashboard.views._annotate_template_in_background",
        ) as mock_bg:
            home_html = "<a href='./privacy-policy.html'>P</a>"
            sibling_html = "<html><body>P content</body></html>"
            with self._patch_fetch(home_html, sibling_html):
                r = self.client.post(
                    self.url,
                    data='{"home_url": "https://example.com/"}',
                    content_type="application/json",
                )
        self.assertEqual(r.status_code, 200)
        # The threading.Thread wraps the target; the wrapped function should
        # have been queued for execution exactly once.
        self.assertEqual(mock_bg.call_count, 1)
        kwargs_or_args = mock_bg.call_args[0]
        # Args: (template_id, sibling_html)
        self.assertEqual(len(kwargs_or_args), 2)
