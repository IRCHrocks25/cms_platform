"""Tests for core.services.url_fetch and the dashboard endpoint that wraps it."""
from unittest import mock

import httpx
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.services import url_fetch
from core.services.url_fetch import UrlFetchError, fetch_url_html


def _mock_response(status_code=200, content=b"<html><body>ok</body></html>",
                   content_type="text/html; charset=utf-8"):
    """Build a fake httpx.Response for use with mock.patch on httpx.Client.get."""
    request = httpx.Request("GET", "https://example.com/")
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers={"Content-Type": content_type},
        request=request,
    )


class FetchUrlHtmlTests(TestCase):
    def test_rejects_empty_url(self):
        with self.assertRaises(UrlFetchError):
            fetch_url_html("")

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(UrlFetchError) as ctx:
            fetch_url_html("file:///etc/passwd")
        self.assertIn("http and https", str(ctx.exception))

    def test_rejects_missing_host(self):
        with self.assertRaises(UrlFetchError):
            fetch_url_html("https://")

    def test_returns_html_on_2xx(self):
        with mock.patch.object(httpx.Client, "get", return_value=_mock_response()):
            html = fetch_url_html("https://susan-rabbyv1.pages.dev/")
        self.assertIn("<body>", html)

    def test_rejects_non_html_content_type(self):
        with mock.patch.object(
            httpx.Client, "get",
            return_value=_mock_response(content_type="application/json"),
        ):
            with self.assertRaises(UrlFetchError) as ctx:
                fetch_url_html("https://example.com/api.json")
        self.assertIn("Expected HTML", str(ctx.exception))

    def test_rejects_4xx_5xx(self):
        with mock.patch.object(
            httpx.Client, "get",
            return_value=_mock_response(status_code=404),
        ):
            with self.assertRaises(UrlFetchError) as ctx:
                fetch_url_html("https://example.com/nope")
        self.assertIn("HTTP 404", str(ctx.exception))

    def test_rejects_oversized_body(self):
        big = b"<html>" + b"x" * 3_000_000 + b"</html>"
        with mock.patch.object(
            httpx.Client, "get",
            return_value=_mock_response(content=big),
        ):
            with self.assertRaises(UrlFetchError) as ctx:
                fetch_url_html("https://example.com/", max_bytes=2_000_000)
        self.assertIn("too large", str(ctx.exception).lower())

    def test_translates_timeout(self):
        with mock.patch.object(
            httpx.Client, "get",
            side_effect=httpx.TimeoutException("slow"),
        ):
            with self.assertRaises(UrlFetchError) as ctx:
                fetch_url_html("https://example.com/")
        self.assertIn("timed out", str(ctx.exception).lower())

    def test_translates_too_many_redirects(self):
        with mock.patch.object(
            httpx.Client, "get",
            side_effect=httpx.TooManyRedirects("loop"),
        ):
            with self.assertRaises(UrlFetchError) as ctx:
                fetch_url_html("https://example.com/")
        self.assertIn("redirects", str(ctx.exception).lower())


class TemplateFetchUrlEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            username="op", password="x", is_staff=True,
        )
        self.url = reverse("dashboard:template_fetch_url")

    def test_requires_login(self):
        # No login → middleware redirects to /login/.
        r = self.client.post(
            self.url, data='{"url": "https://example.com/"}',
            content_type="application/json",
        )
        self.assertIn(r.status_code, (302, 403))

    def test_requires_post(self):
        self.client.force_login(self.staff)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 405)

    def test_rejects_invalid_json(self):
        self.client.force_login(self.staff)
        r = self.client.post(self.url, data="not-json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Invalid JSON", r.json()["error"])

    def test_rejects_missing_url(self):
        self.client.force_login(self.staff)
        r = self.client.post(self.url, data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_returns_html_on_success(self):
        self.client.force_login(self.staff)
        with mock.patch.object(
            url_fetch, "fetch_url_html",
            return_value="<html><body>hi from susan</body></html>",
        ):
            r = self.client.post(
                self.url,
                data='{"url": "https://susan-rabbyv1.pages.dev/"}',
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("hi from susan", body["html"])
        self.assertEqual(body["bytes"], len(body["html"]))

    def test_translates_fetch_error_to_400(self):
        self.client.force_login(self.staff)
        with mock.patch.object(
            url_fetch, "fetch_url_html",
            side_effect=UrlFetchError("Server returned HTTP 503."),
        ):
            r = self.client.post(
                self.url,
                data='{"url": "https://busted.example.com/"}',
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("503", r.json()["error"])
