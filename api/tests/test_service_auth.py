"""S03: tenant service credentials never inherit person or platform authority."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from api.service_auth import (
    issue_service_credential,
    resolve_service_token,
    revoke_service_credential,
    rotate_service_credential,
)
from core.models import Template, Tenant


class ServiceCredentialTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("owner", "owner@example.test", "x")
        template = Template.objects.create(name="Service test", html_source="<p>test</p>")
        self.a = Tenant.objects.create(name="A", subdomain="service-a", template=template, owner=owner)
        self.b = Tenant.objects.create(name="B", subdomain="service-b", template=template, owner=owner)

    def test_token_is_hashed_and_bound_to_exactly_one_tenant(self):
        credential, token = issue_service_credential(self.a, scopes={"read", "draft"})
        self.assertNotIn(token, str(credential.__dict__))
        self.assertNotEqual(credential.token_hash, token)
        principal = resolve_service_token(token)
        self.assertIsNotNone(principal)
        self.assertEqual(principal.tenant.pk, self.a.pk)
        self.assertIsNone(principal.for_tenant(self.b))
        self.assertIsNone(principal.user)
        self.assertIsNone(principal.platform_role)
        self.assertIsNone(resolve_service_token(token + "wrong"))

    def test_revoked_and_expired_tokens_are_rejected(self):
        credential, token = issue_service_credential(self.a, scopes={"read"})
        revoke_service_credential(credential)
        self.assertIsNone(resolve_service_token(token))
        _, expired = issue_service_credential(
            self.a, scopes={"read"}, expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertIsNone(resolve_service_token(expired))

    def test_rotation_revokes_old_token_and_preserves_scope_and_tenant(self):
        credential, token = issue_service_credential(self.a, scopes={"read", "draft"})
        replacement, new_token = rotate_service_credential(
            credential, expires_at=timezone.now() + timedelta(days=30)
        )
        self.assertIsNone(resolve_service_token(token))
        principal = resolve_service_token(new_token)
        self.assertEqual(principal.tenant.pk, self.a.pk)
        self.assertEqual(getattr(principal, "scopes"), frozenset({"read", "draft"}))
        self.assertNotEqual(replacement.pk, credential.pk)

    def test_unknown_scope_is_rejected(self):
        with self.assertRaises(ValueError):
            issue_service_credential(self.a, scopes={"read", "superuser"})
