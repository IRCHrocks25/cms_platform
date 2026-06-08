"""CHIPS Partitioned attribute is added to session + CSRF cookies when
iframe embedding is on. Without this, modern Chrome blocks cookies in
cross-site iframes despite SameSite=None; Secure."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings


User = get_user_model()


class PartitionedCookieTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u", password="x")
        self.client = Client()

    @override_settings(IFRAME_EMBED=True)
    def test_session_cookie_is_partitioned_when_embed_enabled(self):
        from django.test import RequestFactory
        from django.http import HttpResponse
        from core.middleware import PartitionedCookieMiddleware
        rf = RequestFactory()
        request = rf.get("/")
        def view(_req):
            resp = HttpResponse()
            resp.set_cookie("sessionid", "xyz", samesite="None", secure=True)
            return resp
        r = PartitionedCookieMiddleware(view)(request)
        morsel = r.cookies.get("sessionid")
        self.assertIsNotNone(morsel)
        self.assertTrue(morsel.get("partitioned"))
        self.assertIn("Partitioned", morsel.OutputString())

    @override_settings(IFRAME_EMBED=True)
    def test_csrf_cookie_is_partitioned_when_embed_enabled(self):
        # /embed/ returns 404 with no template, but Django still emits the
        # csrftoken on responses where the view didn't explicitly bypass it.
        # We force a CSRF cookie by hitting an endpoint that lazy-sets one.
        from django.middleware.csrf import get_token
        from django.test import RequestFactory
        from django.http import HttpResponse
        rf = RequestFactory()
        request = rf.get("/")
        # Build a minimal response with a CSRF cookie and run the middleware
        # over it directly.
        from core.middleware import PartitionedCookieMiddleware
        def view(_req):
            resp = HttpResponse()
            resp.set_cookie("csrftoken", "abc123")
            return resp
        mw = PartitionedCookieMiddleware(view)
        r = mw(request)
        morsel = r.cookies.get("csrftoken")
        self.assertIsNotNone(morsel)
        self.assertTrue(morsel.get("partitioned"))
        raw = morsel.OutputString()
        self.assertIn("Partitioned", raw)

    @override_settings(IFRAME_EMBED=False)
    def test_no_partitioned_in_dev(self):
        from django.test import RequestFactory
        from django.http import HttpResponse
        from core.middleware import PartitionedCookieMiddleware
        rf = RequestFactory()
        request = rf.get("/")
        def view(_req):
            resp = HttpResponse()
            resp.set_cookie("csrftoken", "abc123")
            return resp
        r = PartitionedCookieMiddleware(view)(request)
        morsel = r.cookies.get("csrftoken")
        self.assertFalse(morsel.get("partitioned"))
