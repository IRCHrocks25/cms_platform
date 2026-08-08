from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from core.models import Template, Tenant, TenantMembership


User = get_user_model()

CLAUDE_CLIENT_ID = "claude-test"


def _make_tenant(owner, *, name, subdomain):
    tpl = Template.objects.create(name=f"tpl-{subdomain}", html_source="<div></div>")
    return Tenant.objects.create(
        name=name,
        subdomain=subdomain,
        template=tpl,
        owner=owner,
    )


def _make_token(
    user,
    *,
    expires_delta=timedelta(hours=1),
    token="tok-test",
    application=None,
):
    if application is None:
        app, _ = Application.objects.get_or_create(
            name="Claude",
            defaults={
                "client_id": CLAUDE_CLIENT_ID,
                "client_type": Application.CLIENT_CONFIDENTIAL,
                "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
                "redirect_uris": "https://example.test/callback",
            },
        )
        if app.client_id != CLAUDE_CLIENT_ID:
            app.client_id = CLAUDE_CLIENT_ID
            app.save(update_fields=["client_id"])
        application = app
    return AccessToken.objects.create(
        user=user,
        application=application,
        token=token,
        expires=timezone.now() + expires_delta,
        scope="read write",
    )


@override_settings(CLAUDE_OAUTH_CLIENT_ID=CLAUDE_CLIENT_ID)
class ResolveAccessTokenTests(TestCase):
    def test_superuser_resolves_platform_wide(self):
        from api.auth import resolve_access_token

        user = User.objects.create_superuser("admin", "a@ex.com", "x")
        _make_token(user, token="tok-super")

        principal = resolve_access_token("tok-super")

        self.assertIsNotNone(principal)
        self.assertEqual(principal.user, user)
        self.assertEqual(principal.platform_role, "superadmin")
        self.assertEqual(principal.tenant_scopes, ())

    def test_staff_without_membership_resolves_to_nothing(self):
        from api.auth import resolve_access_token

        user = User.objects.create_user("ops", "o@ex.com", "x", is_staff=True)
        _make_token(user, token="tok-staff-none")

        self.assertIsNone(resolve_access_token("tok-staff-none"))

    def test_staff_with_membership_resolves_only_that_tenant(self):
        from api.auth import resolve_access_token

        owner = User.objects.create_user("owner", "own@ex.com", "x")
        staff = User.objects.create_user("ops", "o@ex.com", "x", is_staff=True)
        a = _make_tenant(owner, name="Alpha", subdomain="alpha")
        b = _make_tenant(owner, name="Beta", subdomain="beta")
        TenantMembership.objects.create(
            tenant=a, user=staff, role=TenantMembership.ROLE_EDITOR
        )
        _make_token(staff, token="tok-staff-a")

        principal = resolve_access_token("tok-staff-a")

        self.assertIsNotNone(principal)
        self.assertIsNone(principal.platform_role)
        self.assertEqual(len(principal.tenant_scopes), 1)
        self.assertEqual(principal.tenant_scopes[0].tenant, a)
        self.assertIsNotNone(principal.for_tenant(a))
        self.assertIsNone(principal.for_tenant(b))

    def test_superuser_without_membership_still_resolves_any_tenant(self):
        from api.auth import resolve_access_token

        owner = User.objects.create_user("owner", "own@ex.com", "x")
        admin = User.objects.create_superuser("admin", "a@ex.com", "x")
        tenant = _make_tenant(owner, name="Acme", subdomain="acme")
        _make_token(admin, token="tok-super-any")

        principal = resolve_access_token("tok-super-any")

        self.assertIsNotNone(principal)
        self.assertEqual(principal.platform_role, "superadmin")
        self.assertEqual(principal.tenant_scopes, ())
        scope = principal.for_tenant(tenant)
        self.assertIsNotNone(scope)
        self.assertEqual(scope.role, "superadmin")

    def test_token_from_other_oauth_application_rejected(self):
        from api.auth import resolve_access_token

        owner = User.objects.create_user("owner", "own@ex.com", "x")
        member = User.objects.create_user("editor", "e@ex.com", "x")
        tenant = _make_tenant(owner, name="Acme", subdomain="acme")
        TenantMembership.objects.create(
            tenant=tenant, user=member, role=TenantMembership.ROLE_EDITOR
        )
        other_app = Application.objects.create(
            name="Other Client",
            client_id="other-client",
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="https://other.test/callback",
        )
        _make_token(member, token="tok-other-app", application=other_app)

        self.assertIsNone(resolve_access_token("tok-other-app"))

    def test_single_tenant_member_resolves_membership(self):
        from api.auth import resolve_access_token

        owner = User.objects.create_user("owner", "o@ex.com", "x")
        member = User.objects.create_user("editor", "e@ex.com", "x")
        tenant = _make_tenant(owner, name="Acme", subdomain="acme")
        TenantMembership.objects.create(
            tenant=tenant, user=member, role=TenantMembership.ROLE_EDITOR
        )
        _make_token(member, token="tok-member")

        principal = resolve_access_token("tok-member")

        self.assertIsNotNone(principal)
        self.assertIsNone(principal.platform_role)
        self.assertEqual(len(principal.tenant_scopes), 1)
        self.assertEqual(principal.tenant_scopes[0].tenant, tenant)
        self.assertEqual(principal.tenant_scopes[0].role, TenantMembership.ROLE_EDITOR)
        scope = principal.for_tenant(tenant)
        self.assertIsNotNone(scope)
        self.assertEqual(scope.role, TenantMembership.ROLE_EDITOR)

    def test_multi_tenant_member_resolves_all_memberships(self):
        from api.auth import resolve_access_token

        owner = User.objects.create_user("owner", "o@ex.com", "x")
        member = User.objects.create_user("multi", "m@ex.com", "x")
        a = _make_tenant(owner, name="Alpha", subdomain="alpha")
        b = _make_tenant(owner, name="Beta", subdomain="beta")
        TenantMembership.objects.create(
            tenant=a, user=member, role=TenantMembership.ROLE_OWNER
        )
        TenantMembership.objects.create(
            tenant=b, user=member, role=TenantMembership.ROLE_EDITOR
        )
        _make_token(member, token="tok-multi")

        principal = resolve_access_token("tok-multi")

        self.assertIsNotNone(principal)
        roles_by_sub = {s.tenant.subdomain: s.role for s in principal.tenant_scopes}
        self.assertEqual(
            roles_by_sub,
            {"alpha": TenantMembership.ROLE_OWNER, "beta": TenantMembership.ROLE_EDITOR},
        )

    def test_cross_tenant_denial(self):
        from api.auth import resolve_access_token

        owner = User.objects.create_user("owner", "o@ex.com", "x")
        member = User.objects.create_user("only-a", "a@ex.com", "x")
        a = _make_tenant(owner, name="Alpha", subdomain="alpha")
        b = _make_tenant(owner, name="Beta", subdomain="beta")
        TenantMembership.objects.create(
            tenant=a, user=member, role=TenantMembership.ROLE_EDITOR
        )
        _make_token(member, token="tok-a-only")

        principal = resolve_access_token("tok-a-only")

        self.assertIsNotNone(principal.for_tenant(a))
        self.assertIsNone(principal.for_tenant(b))

    def test_non_member_resolves_to_nothing(self):
        from api.auth import resolve_access_token

        user = User.objects.create_user("nobody", "n@ex.com", "x")
        _make_token(user, token="tok-nobody")

        self.assertIsNone(resolve_access_token("tok-nobody"))

    def test_expired_token_denial(self):
        from api.auth import resolve_access_token

        user = User.objects.create_superuser("admin", "a@ex.com", "x")
        _make_token(user, token="tok-exp", expires_delta=timedelta(hours=-1))

        self.assertIsNone(resolve_access_token("tok-exp"))

    def test_revoked_token_denial(self):
        from api.auth import resolve_access_token

        user = User.objects.create_superuser("admin", "a@ex.com", "x")
        token = _make_token(user, token="tok-rev")
        token.revoke()

        self.assertIsNone(resolve_access_token("tok-rev"))

    def test_unknown_token_denial(self):
        from api.auth import resolve_access_token

        self.assertIsNone(resolve_access_token("does-not-exist"))


@override_settings(CLAUDE_OAUTH_CLIENT_ID=CLAUDE_CLIENT_ID)
class CmsBearerAuthTests(TestCase):
    def test_bearer_returns_resolved_principal(self):
        from api.auth import CmsBearerAuth

        user = User.objects.create_superuser("admin", "a@ex.com", "x")
        _make_token(user, token="tok-bearer")
        auth = CmsBearerAuth()
        request = type("R", (), {})()

        principal = auth.authenticate(request, "tok-bearer")

        self.assertIsNotNone(principal)
        self.assertEqual(principal.user, user)
        self.assertEqual(principal.platform_role, "superadmin")

    def test_bearer_rejects_bad_token(self):
        from api.auth import CmsBearerAuth

        auth = CmsBearerAuth()
        request = type("R", (), {})()

        self.assertIsNone(auth.authenticate(request, "bad"))
