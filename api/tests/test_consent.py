import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from oauth2_provider.models import Application

from core.models import Template, Tenant, TenantMembership


User = get_user_model()

REDIRECT_URI = "https://example.test/callback"


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _register_claude_app():
    return Application.objects.create(
        name="Claude",
        client_id="claude-consent-client",
        client_secret="test-only-secret",
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris=REDIRECT_URI,
        skip_authorization=False,
    )


class BuildConsentContextsTests(TestCase):
    def test_member_context_includes_tenant_and_role(self):
        from api.auth import build_consent_contexts

        owner = User.objects.create_user("owner", "o@ex.com", "x")
        member = User.objects.create_user("editor", "e@ex.com", "x")
        tpl = Template.objects.create(name="T", html_source="<div></div>")
        tenant = Tenant.objects.create(
            name="Acme Cafe", subdomain="acme", template=tpl, owner=owner
        )
        TenantMembership.objects.create(
            tenant=tenant, user=member, role=TenantMembership.ROLE_EDITOR
        )

        contexts = build_consent_contexts(member)

        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["tenant_name"], "Acme Cafe")
        self.assertEqual(contexts[0]["role"], TenantMembership.ROLE_EDITOR)
        self.assertFalse(contexts[0]["platform"])

    def test_superuser_context_includes_platform_role(self):
        from api.auth import build_consent_contexts

        admin = User.objects.create_superuser("admin", "a@ex.com", "x")

        contexts = build_consent_contexts(admin)

        self.assertEqual(len(contexts), 1)
        self.assertTrue(contexts[0]["platform"])
        self.assertEqual(contexts[0]["role"], "superadmin")
        self.assertIsNone(contexts[0]["tenant_name"])

    def test_staff_context_shows_memberships_only(self):
        from api.auth import build_consent_contexts

        owner = User.objects.create_user("owner", "o@ex.com", "x")
        staff = User.objects.create_user("ops", "ops@ex.com", "x", is_staff=True)
        tpl = Template.objects.create(name="T", html_source="<div></div>")
        tenant = Tenant.objects.create(
            name="Acme Cafe", subdomain="acme", template=tpl, owner=owner
        )
        TenantMembership.objects.create(
            tenant=tenant, user=staff, role=TenantMembership.ROLE_EDITOR
        )

        contexts = build_consent_contexts(staff)

        self.assertEqual(len(contexts), 1)
        self.assertFalse(contexts[0]["platform"])
        self.assertEqual(contexts[0]["tenant_name"], "Acme Cafe")
        self.assertEqual(contexts[0]["role"], TenantMembership.ROLE_EDITOR)
        self.assertFalse(any(c.get("platform") for c in contexts))


@override_settings(
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
)
class ConsentAuthorizeViewTests(TestCase):
    def setUp(self):
        self.app = _register_claude_app()
        self.owner = User.objects.create_user("owner", "o@ex.com", "password123")
        tpl = Template.objects.create(name="T", html_source="<div></div>")
        self.tenant = Tenant.objects.create(
            name="Acme Cafe", subdomain="acme", template=tpl, owner=self.owner
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role=TenantMembership.ROLE_OWNER,
        )
        self.verifier, self.challenge = _pkce_pair()

    def _authorize_query(self):
        return {
            "response_type": "code",
            "client_id": self.app.client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": "read",
            "state": "consent-state",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
        }

    def test_consent_template_receives_tenant_and_role_context(self):
        self.client.force_login(self.owner)

        response = self.client.get("/authorize/", self._authorize_query())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "oauth2_provider/authorize.html")
        contexts = response.context["consent_contexts"]
        self.assertEqual(contexts[0]["tenant_name"], "Acme Cafe")
        self.assertEqual(contexts[0]["role"], TenantMembership.ROLE_OWNER)
        self.assertContains(response, "Acme Cafe")
        self.assertContains(response, "owner")

    def test_authorization_code_pkce_consent_flow_returns_code(self):
        self.assertTrue(self.client.login(username="owner", password="password123"))

        get_resp = self.client.get("/authorize/", self._authorize_query())
        self.assertEqual(get_resp.status_code, 200)
        self.assertContains(get_resp, "Acme Cafe")

        form = get_resp.context["form"]
        post_data = {
            "redirect_uri": form["redirect_uri"].value(),
            "scope": form["scope"].value(),
            "client_id": form["client_id"].value(),
            "state": form["state"].value(),
            "response_type": form["response_type"].value(),
            "code_challenge": form["code_challenge"].value(),
            "code_challenge_method": form["code_challenge_method"].value(),
            "allow": "Authorize",
        }
        # Optional hidden fields the form may include.
        for name in ("nonce", "claims", "resource"):
            if name in form.fields and form[name].value() not in (None, ""):
                post_data[name] = form[name].value()

        post_resp = self.client.post("/authorize/", post_data)
        self.assertEqual(post_resp.status_code, 302)
        location = post_resp["Location"]
        self.assertTrue(location.startswith(REDIRECT_URI))
        params = parse_qs(urlparse(location).query)
        self.assertIn("code", params)
        self.assertTrue(params["code"][0])
        self.assertEqual(params.get("state", [None])[0], "consent-state")
