from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from core.middleware import PartitionedCookieMiddleware


def _response_for(host, *, cookie_domain=".sites.katek.app"):
    """Run the middleware over a response that set session+csrf cookies with the
    parent Domain, from a request on `host`. Returns the processed response."""
    request = RequestFactory().get("/", HTTP_HOST=host)

    def get_response(_req):
        resp = HttpResponse("ok")
        if cookie_domain:
            resp.set_cookie("sessionid", "s", domain=cookie_domain)
            resp.set_cookie("csrftoken", "c", domain=cookie_domain)
        else:
            resp.set_cookie("sessionid", "s")
            resp.set_cookie("csrftoken", "c")
        return resp

    return PartitionedCookieMiddleware(get_response)(request)


@override_settings(SESSION_COOKIE_DOMAIN=".sites.katek.app", IFRAME_EMBED=True)
class HostAwareCookieDomainTests(TestCase):
    def test_tenant_subdomain_keeps_parent_domain(self):
        r = _response_for("robyn-ladinsky.sites.katek.app")
        self.assertEqual(r.cookies["sessionid"]["domain"], ".sites.katek.app")
        self.assertEqual(r.cookies["csrftoken"]["domain"], ".sites.katek.app")

    def test_agency_apex_keeps_parent_domain(self):
        r = _response_for("sites.katek.app")
        self.assertEqual(r.cookies["sessionid"]["domain"], ".sites.katek.app")

    def test_custom_domain_becomes_host_only(self):
        r = _response_for("robyncoaching.com")
        self.assertEqual(r.cookies["sessionid"]["domain"], "")
        self.assertEqual(r.cookies["csrftoken"]["domain"], "")
        # No Domain= attribute in the emitted Set-Cookie -> host-only cookie.
        self.assertNotIn("Domain=", r.cookies["sessionid"].OutputString())
        self.assertNotIn("Domain=", r.cookies["csrftoken"].OutputString())

    def test_lookalike_domain_is_not_treated_as_parent(self):
        # notsites.katek.app is NOT under .sites.katek.app -> host-only.
        r = _response_for("evil-sites.katek.app")
        self.assertEqual(r.cookies["sessionid"]["domain"], "")


@override_settings(SESSION_COOKIE_DOMAIN=".sites.katek.app", IFRAME_EMBED=False)
class HostAwareCookieDomainWithoutEmbedTests(TestCase):
    def test_domain_scoping_runs_regardless_of_embed(self):
        r = _response_for("robyncoaching.com")
        self.assertEqual(r.cookies["sessionid"]["domain"], "")


@override_settings(SESSION_COOKIE_DOMAIN="", IFRAME_EMBED=False)
class NoParentDomainTests(TestCase):
    def test_noop_when_no_parent_domain_configured(self):
        # Local dev: no COOKIE_PARENT_DOMAIN -> nothing to scope, host-only anyway.
        r = _response_for("localhost", cookie_domain="")
        self.assertEqual(r.cookies["sessionid"]["domain"], "")
