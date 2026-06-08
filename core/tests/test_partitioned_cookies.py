"""CHIPS Partitioned attribute is added to session + CSRF cookies when
iframe embedding is on. Without this, modern Chrome blocks cookies in
cross-site iframes despite SameSite=None; Secure.

These tests drive the REAL middleware chain via Django's test Client
because a synthetic single-middleware test passes even when the cookie
middleware is mis-ordered relative to Session/Csrf — which was the bug
that shipped in 7402e5b. End-to-end is the only correct shape here.
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings


User = get_user_model()


class PartitionedCookieEndToEndTests(TestCase):
    """Drives the full WSGI stack so we catch ordering bugs."""

    def setUp(self):
        self.user = User.objects.create_user(username="u", password="x")

    @override_settings(IFRAME_EMBED=True)
    def test_csrftoken_carries_partitioned_in_real_chain(self):
        # /admin/login/ renders a {% csrf_token %} form so Django reliably
        # writes the csrftoken cookie via CsrfViewMiddleware.process_response.
        # This is the exact codepath that produces the header in production.
        client = Client(enforce_csrf_checks=True)
        r = client.get("/admin/login/")
        morsel = r.cookies.get("csrftoken")
        self.assertIsNotNone(
            morsel, "Django didn't emit csrftoken on /admin/login/ — fixture broken"
        )
        self.assertIn("Partitioned", morsel.OutputString())

    @override_settings(IFRAME_EMBED=True)
    def test_sessionid_carries_partitioned_in_real_chain(self):
        # Log in via the real /admin/login/ POST so SessionMiddleware writes
        # a fresh sessionid via process_response (force_login skips that).
        from django.contrib.auth.models import User as DjangoUser
        DjangoUser.objects.filter(username="admin").delete()
        DjangoUser.objects.create_superuser("admin", "a@x.com", "pw12345!")
        client = Client()
        # Seed csrftoken
        client.get("/admin/login/")
        csrftoken = client.cookies["csrftoken"].value
        r = client.post(
            "/admin/login/",
            {
                "username": "admin",
                "password": "pw12345!",
                "csrfmiddlewaretoken": csrftoken,
                "next": "/admin/",
            },
        )
        morsel = r.cookies.get("sessionid")
        self.assertIsNotNone(
            morsel, "login POST didn't set sessionid — fixture broken"
        )
        self.assertIn("Partitioned", morsel.OutputString())

    @override_settings(IFRAME_EMBED=False)
    def test_no_partitioned_when_iframe_embed_disabled(self):
        client = Client()
        r = client.get("/admin/login/")
        morsel = r.cookies.get("csrftoken")
        self.assertIsNotNone(morsel)
        self.assertNotIn("Partitioned", morsel.OutputString())


class PartitionedCookieMiddlewareOrderingTests(TestCase):
    """Catches the bug we just shipped: middleware below Session/Csrf is a
    no-op. We assert the middleware emits a startup warning if mis-ordered."""

    def test_warns_when_below_session_or_csrf(self):
        import logging
        from core.middleware import PartitionedCookieMiddleware
        bad_order = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "core.middleware.PartitionedCookieMiddleware",
        ]
        with override_settings(IFRAME_EMBED=True, MIDDLEWARE=bad_order):
            with self.assertLogs("core.middleware", level=logging.WARNING) as cm:
                PartitionedCookieMiddleware(lambda r: None)
        self.assertTrue(any("positioned BELOW" in msg for msg in cm.output))

    def test_no_warning_when_above_session_and_csrf(self):
        import logging
        from core.middleware import PartitionedCookieMiddleware
        good_order = [
            "core.middleware.PartitionedCookieMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
        ]
        with override_settings(IFRAME_EMBED=True, MIDDLEWARE=good_order):
            logger = logging.getLogger("core.middleware")
            level = logger.level
            logger.setLevel(logging.WARNING)
            try:
                with self.assertNoLogs("core.middleware", level=logging.WARNING):
                    PartitionedCookieMiddleware(lambda r: None)
            finally:
                logger.setLevel(level)
