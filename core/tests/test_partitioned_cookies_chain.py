"""End-to-end: when IFRAME_EMBED is on, the FULL middleware chain emits
session + csrf cookies with the CHIPS ``Partitioned`` attribute.

The unit test in ``test_partitioned_cookies`` constructs
``PartitionedCookieMiddleware`` around a hand-rolled view that calls
``set_cookie`` inline. That arrangement passes whether the middleware is
at the top or the bottom of ``MIDDLEWARE`` — because the cookie is
already on the response when the middleware's response phase runs.

In production, ``SessionMiddleware`` and ``CsrfViewMiddleware`` set
their cookies in their own ``process_response``. If
``PartitionedCookieMiddleware`` is positioned BELOW them in
``MIDDLEWARE`` (= innermost in the chain), its response phase runs
BEFORE theirs and ``response.cookies`` is empty when it looks — so the
attribute is silently dropped. This test drives the real chain via
``Client`` and catches that ordering bug.
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from core.models import Template, Tenant


User = get_user_model()


@override_settings(
    IFRAME_EMBED=True,
    GHL_AUTO_LOGIN=True,
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SAMESITE="None",
    CSRF_COOKIE_SECURE=True,
    ALLOWED_HOSTS=["*"],
)
class PartitionedCookieChainTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(
            username="chainowner", password="x", email="chain@example.com"
        )
        tpl = Template.objects.create(
            name="chain",
            html_source=(
                "<section data-section='h' data-label='H'>"
                "<h1 data-edit='h.t' data-type='text'>x</h1></section>"
            ),
        )
        self.tenant = Tenant.objects.create(
            name="Chain",
            subdomain="chain",
            template=tpl,
            owner=owner,
            is_published=False,
            ghl_location_id="LOC_CHAIN",
        )

    def _embed_response(self):
        c = Client(SERVER_NAME="sites.katek.app", HTTP_X_FORWARDED_PROTO="https")
        return c.get(
            "/embed/?location_id=LOC_CHAIN&email=chain@example.com",
            secure=True,
        )

    def test_session_cookie_carries_partitioned_through_real_chain(self):
        response = self._embed_response()
        morsel = response.cookies.get("sessionid")
        self.assertIsNotNone(morsel, "sessionid was not emitted by /embed/")
        self.assertIn(
            "Partitioned",
            morsel.OutputString(),
            f"sessionid missing Partitioned: {morsel.OutputString()}",
        )

    def test_csrf_cookie_carries_partitioned_through_real_chain(self):
        response = self._embed_response()
        morsel = response.cookies.get("csrftoken")
        self.assertIsNotNone(morsel, "csrftoken was not emitted by /embed/")
        self.assertIn(
            "Partitioned",
            morsel.OutputString(),
            f"csrftoken missing Partitioned: {morsel.OutputString()}",
        )
