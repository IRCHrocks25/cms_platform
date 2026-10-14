import time
import uuid
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousOperation
from django.db import IntegrityError, transaction
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.central_identity import EntitlementDecision, KatekOIDCBackend
from core.models import (
    CentralIdentityLink,
    Template,
    Tenant,
    TenantMembership,
    VerifiedUserEmail,
)


ENABLED_OIDC_SETTINGS = {
    "KATEK_OIDC_ENABLED": True,
    "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://login.example.test/oauth/v2/authorize",
    "OIDC_OP_TOKEN_ENDPOINT": "https://login.example.test/oauth/v2/token",
    "OIDC_OP_USER_ENDPOINT": "https://login.example.test/oidc/v1/userinfo",
    "OIDC_OP_JWKS_ENDPOINT": "https://login.example.test/oauth/v2/keys",
    "OIDC_RP_CLIENT_ID": "cms-client",
    "OIDC_RP_CLIENT_SECRET": "test-secret",
    "KATEK_OIDC_ISSUER": "https://login.example.test",
    "OIDC_RP_SIGN_ALGO": "RS256",
    "OIDC_USE_NONCE": True,
    "OIDC_USE_PKCE": True,
    "OIDC_PKCE_CODE_CHALLENGE_METHOD": "S256",
}


class CentralIdentityFeatureGateTests(TestCase):
    def test_existing_login_is_unchanged_when_disabled(self):
        response = Client(HTTP_HOST="localhost").get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Continue with Katek")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')

    @override_settings(**ENABLED_OIDC_SETTINGS)
    def test_enabled_login_adds_katek_action_without_replacing_password_form(self):
        response = Client(HTTP_HOST="localhost").get(
            reverse("login"), {"next": "/authorize/?client_id=external-mcp"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Continue with Katek")
        self.assertContains(response, reverse("katek_oidc_start"))
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')

    def test_disabled_start_callback_and_link_routes_return_404(self):
        client = Client(HTTP_HOST="localhost")

        for route_name in (
            "katek_oidc_start",
            "katek_oidc_callback",
            "katek_oidc_link",
        ):
            with self.subTest(route_name=route_name):
                self.assertEqual(client.get(reverse(route_name)).status_code, 404)


@override_settings(**ENABLED_OIDC_SETTINGS)
class CentralIdentityProtocolStartTests(TestCase):
    def test_sign_in_start_uses_state_nonce_and_pkce_s256(self):
        client = Client(HTTP_HOST="acme.localhost")

        response = client.get(
            reverse("katek_oidc_start"),
            {"next": "/authorize/?client_id=external-mcp"},
        )

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "login.example.test")
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], ["cms-client"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertTrue(query["code_challenge"][0])
        self.assertTrue(query["nonce"][0])
        self.assertTrue(query["state"][0])
        self.assertEqual(
            query["redirect_uri"],
            ["http://acme.localhost/auth/katek/callback/"],
        )
        state_record = client.session["oidc_states"][query["state"][0]]
        self.assertNotEqual(state_record["code_verifier"], query["code_challenge"][0])

    def test_cross_host_next_is_not_preserved(self):
        client = Client(HTTP_HOST="acme.localhost")

        client.get(
            reverse("katek_oidc_start"),
            {"next": "https://attacker.example/steal"},
        )

        self.assertIsNone(client.session.get("oidc_login_next"))

    def test_link_requires_recent_legacy_login(self):
        user = get_user_model().objects.create_user(
            username="member", email="member@example.com", password="secret"
        )
        client = Client(HTTP_HOST="acme.localhost")
        client.force_login(user)

        missing = client.get(reverse("katek_oidc_link"))
        self.assertEqual(missing.status_code, 403)

        session = client.session
        session["legacy_authenticated_at"] = int(time.time()) - (16 * 60)
        session["legacy_authenticated_user_id"] = str(user.pk)
        session.save()
        stale = client.get(reverse("katek_oidc_link"))
        self.assertEqual(stale.status_code, 403)

    def test_link_forces_fresh_provider_login_after_verified_local_login(self):
        user = get_user_model().objects.create_user(
            username="verified-member",
            email="verified@example.com",
            password="secret",
        )
        VerifiedUserEmail.objects.create(
            user=user,
            normalized_email="verified@example.com",
            source="test",
        )
        client = Client(HTTP_HOST="acme.localhost")
        client.force_login(user)
        session = client.session
        session["legacy_authenticated_at"] = time.time()
        session["legacy_authenticated_user_id"] = str(user.pk)
        session.save()

        response = client.get(reverse("katek_oidc_link"))

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["prompt"], ["login"])
        self.assertEqual(query["max_age"], ["0"])
        pending = client.session["katek_oidc_transactions"][query["state"][0]]
        self.assertEqual(pending["mode"], "link")
        self.assertEqual(pending["user_id"], str(user.pk))
        self.assertEqual(pending["email"], "verified@example.com")


class CentralIdentityPersistenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.owner = user_model.objects.create_user(
            username="owner", email="owner@example.com", password="secret"
        )
        cls.other = user_model.objects.create_user(
            username="other", email="other@example.com", password="secret"
        )
        template = Template.objects.create(
            name="OIDC template",
            html_source="<section data-section='hero' data-label='Hero'></section>",
        )
        cls.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=template,
            owner=cls.owner,
            global_tenant_id=uuid.uuid4(),
        )
        TenantMembership.objects.create(tenant=cls.tenant, user=cls.owner)

    def test_identity_is_keyed_by_exact_issuer_and_subject(self):
        link = CentralIdentityLink.objects.create(
            user=self.owner,
            issuer="https://login.example.test",
            subject="central-user-1",
            email_at_link="owner@example.com",
        )

        self.assertEqual(
            CentralIdentityLink.objects.get(
                issuer="https://login.example.test", subject="central-user-1"
            ),
            link,
        )
        self.assertFalse(
            CentralIdentityLink.objects.filter(
                issuer="https://other.example.test", subject="central-user-1"
            ).exists()
        )

    def test_subject_cannot_be_linked_to_two_local_users(self):
        CentralIdentityLink.objects.create(
            user=self.owner,
            issuer="https://login.example.test",
            subject="central-user-1",
            email_at_link="owner@example.com",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CentralIdentityLink.objects.create(
                user=self.other,
                issuer="https://login.example.test",
                subject="central-user-1",
                email_at_link="other@example.com",
            )

    def test_user_cannot_have_two_subjects_for_one_issuer(self):
        CentralIdentityLink.objects.create(
            user=self.owner,
            issuer="https://login.example.test",
            subject="central-user-1",
            email_at_link="owner@example.com",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CentralIdentityLink.objects.create(
                user=self.owner,
                issuer="https://login.example.test",
                subject="central-user-2",
                email_at_link="owner@example.com",
            )

    def test_local_email_verification_is_explicit(self):
        self.assertFalse(VerifiedUserEmail.objects.filter(user=self.owner).exists())

        proof = VerifiedUserEmail.objects.create(
            user=self.owner,
            normalized_email="owner@example.com",
            source="provisioning",
        )

        self.assertEqual(proof.normalized_email, "owner@example.com")


@override_settings(**ENABLED_OIDC_SETTINGS)
class CentralIdentityTokenValidationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cls.other_private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )

    def _claims(self, **overrides):
        claims = {
            "iss": "https://login.example.test",
            "aud": "cms-client",
            "sub": "central-user-1",
            "nonce": "expected-nonce",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        }
        claims.update(overrides)
        return claims

    def _verify(self, token, **settings_overrides):
        with override_settings(
            OIDC_RP_IDP_SIGN_KEY=self.public_key,
            **settings_overrides,
        ):
            return KatekOIDCBackend().verify_token(token, nonce="expected-nonce")

    def test_accepts_exact_rs256_issuer_audience_signature_expiry_and_nonce(self):
        token = jwt.encode(self._claims(), self.private_key, algorithm="RS256")

        payload = self._verify(token)

        self.assertEqual(payload["sub"], "central-user-1")

    def test_rejects_foreign_key_none_hs256_wrong_claims_and_expiry(self):
        bad_tokens = {
            "foreign-key": jwt.encode(
                self._claims(), self.other_private_key, algorithm="RS256"
            ),
            "none": jwt.encode(self._claims(), key="", algorithm="none"),
            "hs256": jwt.encode(
                self._claims(), key="shared-secret-at-least-thirty-two-bytes", algorithm="HS256"
            ),
            "issuer": jwt.encode(
                self._claims(iss="https://foreign.example.test"),
                self.private_key,
                algorithm="RS256",
            ),
            "audience": jwt.encode(
                self._claims(aud="other-client"),
                self.private_key,
                algorithm="RS256",
            ),
            "authorized-party": jwt.encode(
                self._claims(aud=["cms-client", "other-client"], azp="other-client"),
                self.private_key,
                algorithm="RS256",
            ),
            "expired": jwt.encode(
                self._claims(exp=int(time.time()) - 30),
                self.private_key,
                algorithm="RS256",
            ),
            "missing-expiry": jwt.encode(
                {key: value for key, value in self._claims().items() if key != "exp"},
                self.private_key,
                algorithm="RS256",
            ),
            "nonce": jwt.encode(
                self._claims(nonce="wrong-nonce"),
                self.private_key,
                algorithm="RS256",
            ),
        }

        for label, token in bad_tokens.items():
            with self.subTest(label=label), self.assertRaises(SuspiciousOperation):
                self._verify(token)


@override_settings(**ENABLED_OIDC_SETTINGS)
class CentralIdentityMappingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="member", email="member@example.com", password="secret"
        )
        cls.other = get_user_model().objects.create_user(
            username="other", email="other@example.com", password="secret"
        )
        template = Template.objects.create(
            name="Central identity mapping template",
            html_source="<section data-section='hero' data-label='Hero'></section>",
        )
        cls.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=template,
            owner=cls.user,
            global_tenant_id=uuid.uuid4(),
        )
        TenantMembership.objects.create(tenant=cls.tenant, user=cls.user)

    def _request(self, *, mode, user=None, age=0):
        request = RequestFactory().get(
            reverse("katek_oidc_callback"), {"state": "state-1", "code": "code"},
            HTTP_HOST="acme.localhost",
        )
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request.user = user or get_user_model()()
        request.tenant = self.tenant
        request.session["katek_oidc_transactions"] = {
            "state-1": {
                "mode": mode,
                "host": "acme.localhost",
                "created_at": time.time() - age,
                "user_id": str(user.pk) if user else None,
                "email": "member@example.com" if user else None,
            }
        }
        return request

    def _claims(self, **overrides):
        claims = {
            "iss": "https://login.example.test",
            "sub": "central-user-1",
            "email": "member@example.com",
            "email_verified": True,
            "auth_time": int(time.time()),
        }
        claims.update(overrides)
        return claims

    def _registry(self):
        registry = Mock()
        registry.require_active.return_value = EntitlementDecision(
            global_tenant_id=self.tenant.global_tenant_id,
            local_tenant_id=str(self.tenant.pk),
            revision="11",
            updated_at="2026-10-02T00:00:00Z",
        )
        return registry

    def test_unknown_subject_never_auto_links_by_matching_email(self):
        backend = KatekOIDCBackend()
        backend.request = self._request(mode="signin")
        backend.get_userinfo = Mock(return_value=self._claims())

        with self.assertRaisesMessage(SuspiciousOperation, "not linked"):
            backend.get_or_create_user("access", "id", self._claims())
        self.assertFalse(CentralIdentityLink.objects.exists())

    def test_userinfo_must_repeat_the_signed_id_token_subject(self):
        backend = KatekOIDCBackend()
        backend.request = self._request(mode="signin")
        backend.get_userinfo = Mock(
            return_value=self._claims(sub="different-central-user")
        )

        with self.assertRaisesMessage(SuspiciousOperation, "subject mismatch"):
            backend.get_or_create_user("access", "id", self._claims())

    def test_fresh_link_requires_explicit_verified_email_and_then_maps_subject(self):
        request = self._request(mode="link", user=self.user)
        request.session["legacy_authenticated_at"] = time.time()
        request.session["legacy_authenticated_user_id"] = str(self.user.pk)
        backend = KatekOIDCBackend()
        backend.request = request
        backend.get_userinfo = Mock(return_value=self._claims())

        with self.assertRaisesMessage(SuspiciousOperation, "not verified"):
            backend.get_or_create_user("access", "id", self._claims())

        VerifiedUserEmail.objects.create(
            user=self.user,
            normalized_email="member@example.com",
            source="test",
        )
        backend.request = self._request(mode="link", user=self.user)
        backend.request.session["legacy_authenticated_at"] = time.time()
        backend.request.session["legacy_authenticated_user_id"] = str(self.user.pk)
        backend.get_userinfo = Mock(return_value=self._claims())
        with patch("core.central_identity.tenant_registry_client", return_value=self._registry()):
            resolved = backend.get_or_create_user("access", "id", self._claims())

        self.assertEqual(resolved, self.user)
        self.assertTrue(
            CentralIdentityLink.objects.filter(
                user=self.user,
                issuer="https://login.example.test",
                subject="central-user-1",
            ).exists()
        )
        self.assertEqual(
            backend.request.session["katek_central_identity"]["bindings"][0]["tenant_id"],
            str(self.tenant.pk),
        )

    def test_link_rejects_stale_idp_login_and_email_mismatch(self):
        VerifiedUserEmail.objects.create(
            user=self.user,
            normalized_email="member@example.com",
            source="test",
        )
        cases = {
            "stale": self._claims(auth_time=int(time.time()) - 301),
            "mismatch": self._claims(email="attacker@example.com"),
            "unverified": self._claims(email_verified=False),
        }
        for label, claims in cases.items():
            request = self._request(mode="link", user=self.user)
            request.session["legacy_authenticated_at"] = time.time()
            request.session["legacy_authenticated_user_id"] = str(self.user.pk)
            backend = KatekOIDCBackend()
            backend.request = request
            backend.get_userinfo = Mock(return_value=claims)
            with self.subTest(label=label), self.assertRaises(SuspiciousOperation):
                backend.get_or_create_user("access", "id", claims)

    def test_link_does_not_change_django_tenant_role(self):
        membership = TenantMembership.objects.get(tenant=self.tenant, user=self.user)
        self.assertEqual(membership.role, TenantMembership.ROLE_EDITOR)

        CentralIdentityLink.objects.create(
            user=self.user,
            issuer="https://login.example.test",
            subject="central-user-1",
            email_at_link="member@example.com",
        )
        backend = KatekOIDCBackend()
        backend.request = self._request(mode="signin")
        backend.get_userinfo = Mock(return_value=self._claims())
        with patch("core.central_identity.tenant_registry_client", return_value=self._registry()):
            resolved = backend.get_or_create_user("access", "id", self._claims())

        self.assertEqual(resolved, self.user)
        membership.refresh_from_db()
        self.assertEqual(membership.role, TenantMembership.ROLE_EDITOR)

    def test_sign_in_transaction_is_consumed_once(self):
        CentralIdentityLink.objects.create(
            user=self.user,
            issuer="https://login.example.test",
            subject="central-user-1",
            email_at_link="member@example.com",
        )
        backend = KatekOIDCBackend()
        backend.request = self._request(mode="signin")
        backend.get_userinfo = Mock(return_value=self._claims())

        with patch(
            "core.central_identity.tenant_registry_client",
            return_value=self._registry(),
        ):
            self.assertEqual(
                backend.get_or_create_user("access", "id", self._claims()),
                self.user,
            )

        with self.assertRaisesMessage(SuspiciousOperation, "transaction is missing"):
            backend.get_or_create_user("access", "id", self._claims())

    def test_sign_in_transaction_is_bound_to_host_and_expires(self):
        CentralIdentityLink.objects.create(
            user=self.user,
            issuer="https://login.example.test",
            subject="central-user-1",
            email_at_link="member@example.com",
        )
        cases = {
            "host": self._request(mode="signin"),
            "expired": self._request(mode="signin", age=(10 * 60) + 1),
        }
        cases["host"].META["HTTP_HOST"] = "other.localhost"

        for label, request in cases.items():
            backend = KatekOIDCBackend()
            backend.request = request
            backend.get_userinfo = Mock(return_value=self._claims())
            with self.subTest(label=label), self.assertRaises(SuspiciousOperation):
                backend.get_or_create_user("access", "id", self._claims())

    def test_linked_subject_cannot_enter_a_tenant_without_local_membership(self):
        CentralIdentityLink.objects.create(
            user=self.other,
            issuer="https://login.example.test",
            subject="central-user-2",
            email_at_link="other@example.com",
        )
        claims = self._claims(
            sub="central-user-2",
            email="other@example.com",
        )
        backend = KatekOIDCBackend()
        backend.request = self._request(mode="signin")
        backend.get_userinfo = Mock(return_value=claims)

        with self.assertRaisesMessage(SuspiciousOperation, "entitlement denied"):
            backend.get_or_create_user("access", "id", claims)

    def test_linked_subject_is_denied_when_tenant_has_no_registry_binding(self):
        CentralIdentityLink.objects.create(
            user=self.user,
            issuer="https://login.example.test",
            subject="central-user-1",
            email_at_link="member@example.com",
        )
        self.tenant.global_tenant_id = None
        self.tenant.save(update_fields=["global_tenant_id"])
        backend = KatekOIDCBackend()
        backend.request = self._request(mode="signin")
        backend.get_userinfo = Mock(return_value=self._claims())

        with self.assertRaisesMessage(SuspiciousOperation, "entitlement denied"):
            backend.get_or_create_user("access", "id", self._claims())


@override_settings(
    **ENABLED_OIDC_SETTINGS,
    KATEK_OIDC_ENTITLEMENT_CACHE_SECONDS=300,
)
class CentralIdentitySessionRefreshTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="session-member",
            email="session@example.com",
            password="secret",
        )
        template = Template.objects.create(
            name="Central session template",
            html_source="<section data-section='hero' data-label='Hero'></section>",
        )
        cls.tenant = Tenant.objects.create(
            name="Session Acme",
            subdomain="session-acme",
            template=template,
            owner=cls.user,
            global_tenant_id=uuid.uuid4(),
        )
        TenantMembership.objects.create(tenant=cls.tenant, user=cls.user)
        cls.other_tenant = Tenant.objects.create(
            name="Other Session Acme",
            subdomain="other-session-acme",
            template=template,
            owner=cls.user,
            global_tenant_id=uuid.uuid4(),
        )
        TenantMembership.objects.create(tenant=cls.other_tenant, user=cls.user)

    def _client(self, checked_at):
        client = Client(HTTP_HOST="session-acme.localhost")
        client.force_login(self.user)
        session = client.session
        session["katek_central_identity"] = {
            "user_id": str(self.user.pk),
            "issuer": "https://login.example.test",
            "subject": "central-user-1",
            "bindings": [
                {
                    "tenant_id": str(self.tenant.pk),
                    "global_tenant_id": str(self.tenant.global_tenant_id),
                    "revision": "cached",
                }
            ],
            "checked_at": checked_at,
        }
        session.save()
        return client

    def _registry(self, *, denied=False):
        registry = Mock()
        if denied:
            from core.central_identity import RegistryDenied

            registry.require_active.side_effect = RegistryDenied("suspended")
        else:
            registry.require_active.return_value = EntitlementDecision(
                global_tenant_id=self.tenant.global_tenant_id,
                local_tenant_id=str(self.tenant.pk),
                revision="12",
                updated_at="2026-10-02T00:00:00Z",
            )
        return registry

    def test_success_cache_is_used_for_less_than_five_minutes(self):
        client = self._client(time.time() - 299)
        registry = self._registry()

        with patch("core.central_identity.tenant_registry_client", return_value=registry):
            response = client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        registry.require_active.assert_not_called()

    def test_fresh_cache_does_not_authorize_a_different_tenant(self):
        client = self._client(time.time() - 30)
        client.defaults["HTTP_HOST"] = "other-session-acme.localhost"

        with patch(
            "core.central_identity.tenant_registry_client",
            return_value=self._registry(denied=True),
        ):
            response = client.get(reverse("healthz"))

        self.assertEqual(response.wsgi_request.get_host(), "other-session-acme.localhost")
        self.assertEqual(response.wsgi_request.tenant, self.other_tenant)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", client.session)

    def test_session_load_rechecks_registry_after_five_minutes(self):
        client = self._client(time.time() - 300)
        registry = self._registry()

        with patch("core.central_identity.tenant_registry_client", return_value=registry):
            response = client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        registry.require_active.assert_called_once_with(
            self.tenant.global_tenant_id, str(self.tenant.pk)
        )
        self.assertEqual(
            client.session["katek_central_identity"]["bindings"][0]["revision"],
            "12",
        )

    def test_session_load_fails_closed_when_refresh_is_denied(self):
        client = self._client(time.time() - 301)

        with patch(
            "core.central_identity.tenant_registry_client",
            return_value=self._registry(denied=True),
        ):
            response = client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", client.session)
        self.assertNotIn("katek_central_identity", client.session)
