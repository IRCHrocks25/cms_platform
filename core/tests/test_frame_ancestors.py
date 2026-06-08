"""CSP frame-ancestors is emitted from the app so each new GHL whitelabel
domain only needs an env var change (not a Cloudflare rule edit)."""
import importlib

from django.test import Client, TestCase, override_settings


def _reload_middleware():
    """The middleware caches its header value at __init__ time, so we have to
    rebuild the WSGI stack any time GHL_FRAME_ANCESTORS changes during a test."""
    import django
    import cms_platform.wsgi as wsgi_module
    importlib.reload(wsgi_module)
    django.core.handlers.base.BaseHandler.load_middleware = (
        django.core.handlers.base.BaseHandler.load_middleware
    )


class FrameAncestorsCspTests(TestCase):
    def setUp(self):
        self.client = Client()

    @override_settings(GHL_FRAME_ANCESTORS="https://app.industryrockstars.ch,https://app.daltoleadsystem.com")
    def test_emits_listed_origins(self):
        client = Client()  # fresh client so middleware re-reads settings
        r = client.get("/embed/")  # 404 — no template render, just middleware
        csp = r.get("Content-Security-Policy", "")
        self.assertIn("frame-ancestors", csp)
        self.assertIn("https://app.industryrockstars.ch", csp)
        self.assertIn("https://app.daltoleadsystem.com", csp)
        self.assertIn("'self'", csp)

    @override_settings(GHL_FRAME_ANCESTORS="*")
    def test_wildcard_allows_any_parent(self):
        client = Client()
        r = client.get("/embed/")  # 404 — no template render, just middleware
        self.assertEqual(r.get("Content-Security-Policy"), "frame-ancestors *;")

    @override_settings(GHL_FRAME_ANCESTORS="")
    def test_empty_defaults_to_self_only(self):
        client = Client()
        r = client.get("/embed/")  # 404 — no template render, just middleware
        self.assertEqual(r.get("Content-Security-Policy"), "frame-ancestors 'self';")
