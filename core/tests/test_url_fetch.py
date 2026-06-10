"""Tests for core.services.url_fetch and the dashboard endpoint that wraps it."""
from unittest import mock

import httpx
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.services import url_fetch
from core.services.url_fetch import (
    UrlFetchError,
    fetch_url_html,
    rewrite_relative_urls,
)


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

    def test_rewrites_relative_urls_after_fetch(self):
        page = (
            b"<html><body>"
            b"<a href='./privacy-policy.html'>Privacy</a>"
            b"<img src='/images/hero.jpg'/>"
            b"</body></html>"
        )
        with mock.patch.object(
            httpx.Client, "get",
            return_value=_mock_response(content=page),
        ):
            html = fetch_url_html("https://susan-rabbyv2.pages.dev/")
        self.assertIn("https://example.com/privacy-policy.html", html)  # mock url
        # Actually _mock_response wires request URL to example.com, so use that:
        self.assertIn("https://example.com/images/hero.jpg", html)

    def test_rewrite_can_be_disabled(self):
        page = b"<html><body><a href='./privacy.html'>P</a></body></html>"
        with mock.patch.object(
            httpx.Client, "get",
            return_value=_mock_response(content=page),
        ):
            html = fetch_url_html("https://example.com/", rewrite_urls=False)
        self.assertIn("./privacy.html", html)
        self.assertNotIn("https://example.com/privacy.html", html)


class RewriteRelativeUrlsTests(TestCase):
    BASE = "https://susan-rabbyv2.pages.dev/"

    def test_converts_dot_slash_path(self):
        out = rewrite_relative_urls(
            "<a href='./privacy-policy.html'>P</a>", self.BASE,
        )
        self.assertIn("https://susan-rabbyv2.pages.dev/privacy-policy.html", out)

    def test_converts_root_relative_path(self):
        out = rewrite_relative_urls(
            "<img src='/img/hero.jpg'>", self.BASE,
        )
        self.assertIn("https://susan-rabbyv2.pages.dev/img/hero.jpg", out)

    def test_leaves_absolute_urls_alone(self):
        out = rewrite_relative_urls(
            "<a href='https://other.example.com/x'>x</a>", self.BASE,
        )
        self.assertIn("https://other.example.com/x", out)
        self.assertNotIn(self.BASE + "https", out)

    def test_leaves_protocol_relative_alone(self):
        out = rewrite_relative_urls(
            "<script src='//cdn.example.com/lib.js'></script>", self.BASE,
        )
        self.assertIn("//cdn.example.com/lib.js", out)

    def test_leaves_mailto_tel_javascript_alone(self):
        for href in ("mailto:susan@example.com", "tel:+15551234", "javascript:void(0)", "#section"):
            out = rewrite_relative_urls(f"<a href='{href}'>x</a>", self.BASE)
            self.assertIn(href, out)

    def test_leaves_data_uris_alone(self):
        out = rewrite_relative_urls(
            "<img src='data:image/png;base64,iVBORw'>", self.BASE,
        )
        self.assertIn("data:image/png;base64,iVBORw", out)

    def test_rewrites_multiple_tag_types(self):
        html = (
            "<link rel='stylesheet' href='./styles.css'>"
            "<script src='./bundle.js'></script>"
            "<iframe src='./embed.html'></iframe>"
            "<source src='./video.mp4'>"
        )
        out = rewrite_relative_urls(html, self.BASE)
        for path in ("styles.css", "bundle.js", "embed.html", "video.mp4"):
            self.assertIn(self.BASE + path, out)

    def test_handles_empty_inputs(self):
        self.assertEqual(rewrite_relative_urls("", self.BASE), "")
        self.assertEqual(
            rewrite_relative_urls("<p>hi</p>", ""), "<p>hi</p>",
        )

    def test_home_link_dot_slash_stays_root_relative(self):
        """`<a href='./'>` on a fetched index.html is the brand logo / home
        link. Keep it root-relative so visitors stay on the CMS-hosted site."""
        out = rewrite_relative_urls(
            "<a class='brand' href='./'>Home</a>", self.BASE,
        )
        self.assertIn('href="/"', out)
        self.assertNotIn("susan-rabbyv2.pages.dev", out)

    def test_home_link_index_html_stays_root_relative(self):
        out = rewrite_relative_urls(
            "<a href='./index.html'>Home</a>", self.BASE,
        )
        self.assertIn('href="/"', out)
        self.assertNotIn("susan-rabbyv2.pages.dev", out)

    def test_home_link_slash_stays_root_relative(self):
        out = rewrite_relative_urls(
            "<a href='/'>Home</a>", self.BASE,
        )
        self.assertIn('href="/"', out)
        self.assertNotIn("susan-rabbyv2.pages.dev", out)

    def test_sibling_page_link_goes_to_source_origin(self):
        """Privacy and terms still get rewritten to absolute — they aren't
        the same page as the imported home."""
        out = rewrite_relative_urls(
            "<a href='./privacy-policy.html'>Privacy</a>", self.BASE,
        )
        self.assertIn("https://susan-rabbyv2.pages.dev/privacy-policy.html", out)

    def test_image_on_home_still_goes_to_source_origin(self):
        """Same-page heuristic only applies to <a> tags. An <img> on the
        home page still needs the absolute source URL because the CMS
        isn't hosting the asset."""
        out = rewrite_relative_urls(
            "<img src='./hero.jpg'>", self.BASE,
        )
        self.assertIn("https://susan-rabbyv2.pages.dev/hero.jpg", out)


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
