"""Tests for the SPA-aware additions to the Fetch-from-URL pipeline:
``looks_like_spa_shell`` (heuristic), ``inline_external_assets`` (pure-
Python asset rewriter), and the auto-fallback wiring on the
``template_fetch_url`` view.

Headless rendering itself uses Playwright + chromium and is intentionally
opt-in on the deploy side (the dependency adds ~150MB to the image). The
tests here keep both the production deploy and the test suite free of that
weight by *mocking* the renderer at the import boundary — every behaviour
that matters at the view layer can be exercised by swapping
``render_url_html`` for a stub that returns a fixed string.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.services.url_fetch import (
    UrlFetchError,
    inline_external_assets,
    looks_like_spa_shell,
)


User = get_user_model()


# --------------------------------------------------------------------------- #
# SPA-shell heuristic                                                         #
# --------------------------------------------------------------------------- #


class LooksLikeSpaShellTests(TestCase):
    def test_vite_react_shell_with_div_root_is_spa(self):
        html = (
            "<!doctype html><html><head><title>X</title></head>"
            "<body><div id=\"root\"></div>"
            "<script type=\"module\" src=\"/assets/index.js\"></script>"
            "</body></html>"
        )
        self.assertTrue(looks_like_spa_shell(html))

    def test_nextjs_shell_with_div_next_is_spa(self):
        html = (
            "<!doctype html><html><body>"
            "<div id=\"__next\"></div>"
            "</body></html>"
        )
        self.assertTrue(looks_like_spa_shell(html))

    def test_nuxt_shell_with_div_nuxt_is_spa(self):
        html = (
            "<!doctype html><html><body>"
            "<div id=\"__nuxt\"></div>"
            "</body></html>"
        )
        self.assertTrue(looks_like_spa_shell(html))

    def test_real_static_page_with_long_body_is_not_spa(self):
        # Even with a #root div, substantial visible text means it's not a shell.
        body_text = (
            "Welcome to Acme Bakery. We've been baking since 1996. "
            "Our sourdough is fermented for thirty-six hours. Drop by any day "
            "of the week between seven and noon. Free coffee with any pastry "
            "on Tuesdays. Order ahead for weekend pickups by noon Friday. "
            "Catering for offices and birthdays available on request."
        )
        html = (
            f"<html><body><div id='root'>{body_text}</div></body></html>"
        )
        self.assertFalse(looks_like_spa_shell(html))

    def test_long_static_landing_page_is_not_spa(self):
        # A static landing page with no SPA mount point — never a shell.
        html = (
            "<html><body>"
            "<h1>Acme Bakery</h1>"
            "<p>" + ("Some marketing copy. " * 30) + "</p>"
            "</body></html>"
        )
        self.assertFalse(looks_like_spa_shell(html))

    def test_empty_or_blank_input_is_not_spa(self):
        self.assertFalse(looks_like_spa_shell(""))
        self.assertFalse(looks_like_spa_shell("   "))

    def test_short_body_without_mount_point_is_not_spa(self):
        # Could be a 404 page or a holding page — not necessarily SPA.
        html = "<html><body><p>Coming soon.</p></body></html>"
        self.assertFalse(looks_like_spa_shell(html))


# --------------------------------------------------------------------------- #
# Asset inliner                                                               #
# --------------------------------------------------------------------------- #


def _make_link(href: str) -> str:
    return f'<link rel="stylesheet" href="{href}">'


class InlineExternalAssetsTests(TestCase):
    def test_relative_img_src_becomes_absolute(self):
        html = '<html><body><img src="/assets/hero.png" alt="x"></body></html>'
        out = inline_external_assets(html, base_url="https://kieran.pages.dev/")
        self.assertIn(
            'src="https://kieran.pages.dev/assets/hero.png"', out
        )

    def test_absolute_img_src_unchanged(self):
        html = '<html><body><img src="https://other.example/a.png"></body></html>'
        out = inline_external_assets(html, base_url="https://kieran.pages.dev/")
        self.assertIn('src="https://other.example/a.png"', out)

    def test_data_uri_src_unchanged(self):
        html = '<html><body><img src="data:image/png;base64,iVBORw0..."></body></html>'
        out = inline_external_assets(html, base_url="https://kieran.pages.dev/")
        self.assertIn('src="data:image/png;base64,iVBORw0..."', out)

    def test_srcset_each_entry_absolutized(self):
        html = (
            '<html><body><img src="/a.png" '
            'srcset="/a.png 1x, /a@2x.png 2x"></body></html>'
        )
        out = inline_external_assets(html, base_url="https://k.pages.dev/")
        self.assertIn("https://k.pages.dev/a.png 1x", out)
        self.assertIn("https://k.pages.dev/a@2x.png 2x", out)

    def test_module_script_stripped(self):
        html = (
            '<html><head><script type="module" src="/assets/i.js"></script></head>'
            '<body></body></html>'
        )
        out = inline_external_assets(html, base_url="https://k.pages.dev/")
        self.assertNotIn("<script", out)

    def test_external_src_script_stripped(self):
        html = (
            '<html><head><script src="/assets/i.js"></script></head>'
            '<body></body></html>'
        )
        out = inline_external_assets(html, base_url="https://k.pages.dev/")
        self.assertNotIn("<script", out)

    def test_noindex_meta_stripped(self):
        html = (
            '<html><head>'
            '<meta name="robots" content="noindex, nofollow">'
            '</head><body></body></html>'
        )
        out = inline_external_assets(html, base_url="https://k.pages.dev/")
        self.assertNotIn("robots", out)

    def test_empty_or_blank_input_passes_through(self):
        self.assertEqual(inline_external_assets("", "https://x/"), "")
        self.assertEqual(inline_external_assets("   ", "https://x/"), "   ")

    def test_missing_base_url_passes_through(self):
        # Defensive: don't blow up if the view passes an empty base.
        html = '<body><img src="/a.png"></body>'
        self.assertEqual(inline_external_assets(html, ""), html)

    def test_external_stylesheet_inlined_when_reachable(self):
        link = _make_link("/assets/index.css")
        html = f"<html><head>{link}</head><body></body></html>"

        class _Resp:
            status_code = 200
            text = ".btn { background: url(/img/x.png); color: red; }"

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url):
                return _Resp()

        with patch("core.services.url_fetch.httpx.Client", _Client):
            out = inline_external_assets(html, base_url="https://k.pages.dev/")

        self.assertNotIn("<link", out)
        self.assertIn("data-inlined-from", out)
        # Relative url() inside the CSS becomes absolute on origin.
        self.assertIn("url(https://k.pages.dev/img/x.png)", out)
        self.assertIn("color: red", out)

    def test_external_stylesheet_kept_as_link_when_fetch_fails(self):
        link = _make_link("/assets/index.css")
        html = f"<html><head>{link}</head><body></body></html>"

        class _BoomResp:
            status_code = 500
            text = ""

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url):
                return _BoomResp()

        with patch("core.services.url_fetch.httpx.Client", _Client):
            out = inline_external_assets(html, base_url="https://k.pages.dev/")

        # Falls back to an absolute <link> so the template still works.
        self.assertIn(
            'href="https://k.pages.dev/assets/index.css"', out
        )


# --------------------------------------------------------------------------- #
# View wiring: template_fetch_url auto-fallback                               #
# --------------------------------------------------------------------------- #


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TemplateFetchUrlSpaFallbackTests(TestCase):
    """The view fetches static HTML first. When that looks like an SPA shell
    it re-runs through ``render_url_html`` and stitches assets in via
    ``inline_external_assets``. When ``render_url_html`` is unavailable
    (no Playwright on the deploy), the view returns the static HTML plus
    a warning rather than failing outright.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)

    def _client(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c

    def _post(self, client, body):
        return client.post(
            reverse("dashboard:template_fetch_url"),
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_static_page_returns_html_without_invoking_renderer(self):
        c = self._client()
        rendered_calls: list[str] = []

        def fake_fetch(url, **_kw):
            return (
                "<html><body><h1>About</h1>"
                + ("<p>" + "Lorem ipsum " * 50 + "</p>") * 3
                + "</body></html>"
            )

        def fake_render(url, **_kw):
            rendered_calls.append(url)
            return "<html><body>RENDERED</body></html>"

        with (
            patch("core.services.url_fetch.fetch_url_html", fake_fetch),
            patch("core.services.url_fetch.render_url_html", fake_render),
        ):
            response = self._post(c, {"url": "https://acme.example/"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("<h1>About</h1>", body["html"])
        self.assertFalse(body["rendered_with_js"])
        self.assertNotIn("warning", body)
        self.assertEqual(rendered_calls, [],
                         "static page must not call render_url_html")

    def test_spa_shell_triggers_render_and_inline(self):
        c = self._client()

        def fake_fetch(url, **_kw):
            return (
                "<html><body>"
                "<div id=\"root\"></div>"
                "<script type=\"module\" src=\"/assets/i.js\"></script>"
                "</body></html>"
            )

        def fake_render(url, **_kw):
            return (
                "<!DOCTYPE html><html><body>"
                "<h1>Hydrated headline</h1>"
                "<img src=\"/assets/hero.png\">"
                "</body></html>"
            )

        with (
            patch("core.services.url_fetch.fetch_url_html", fake_fetch),
            patch("core.services.url_fetch.render_url_html", fake_render),
        ):
            response = self._post(c, {"url": "https://kieran.pages.dev/"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["rendered_with_js"])
        self.assertIn("Hydrated headline", body["html"])
        # inline_external_assets absolutized the image src on top of the render.
        self.assertIn(
            "https://kieran.pages.dev/assets/hero.png", body["html"]
        )

    def test_spa_shell_falls_back_with_warning_when_renderer_unavailable(self):
        c = self._client()

        def fake_fetch(url, **_kw):
            return (
                "<html><body><div id=\"root\"></div></body></html>"
            )

        def fake_render(url, **_kw):
            raise UrlFetchError(
                "Server-side JavaScript rendering isn't enabled on this deploy. "
                "Install the optional dependency."
            )

        with (
            patch("core.services.url_fetch.fetch_url_html", fake_fetch),
            patch("core.services.url_fetch.render_url_html", fake_render),
        ):
            response = self._post(c, {"url": "https://acme.example/"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["rendered_with_js"])
        self.assertIn("warning", body)
        self.assertIn("single-page app", body["warning"])
        self.assertIn("JavaScript rendering isn't enabled", body["warning"])
        # And the static HTML is still returned so the operator can paste it manually.
        self.assertIn("<div id=\"root\"></div>", body["html"])

    def test_force_render_bypasses_static_fetch(self):
        c = self._client()
        static_calls: list[str] = []

        def fake_fetch(url, **_kw):
            static_calls.append(url)
            return "<body>STATIC</body>"

        def fake_render(url, **_kw):
            return "<html><body>FORCED</body></html>"

        with (
            patch("core.services.url_fetch.fetch_url_html", fake_fetch),
            patch("core.services.url_fetch.render_url_html", fake_render),
        ):
            response = self._post(
                c,
                {"url": "https://acme.example/", "force_render": True},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["rendered_with_js"])
        self.assertIn("FORCED", body["html"])
        self.assertEqual(static_calls, [],
                         "force_render must skip the static GET")

    def test_anonymous_request_is_rejected(self):
        c = Client(HTTP_HOST="localhost")
        response = self._post(c, {"url": "https://acme.example/"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_missing_url_returns_400(self):
        c = self._client()
        response = self._post(c, {"url": ""})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_invalid_json_returns_400(self):
        c = self._client()
        response = c.post(
            reverse("dashboard:template_fetch_url"),
            data="not-json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
