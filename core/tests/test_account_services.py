from django.test import TestCase

from core.models import Template, TenantMembership
from core.services.accounts import create_scoped_login, create_tenant_account


class AccountServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.template = Template.objects.create(
            name="Service template",
            html_source=(
                "<section data-section='hero' data-label='Hero'>"
                "<h1 data-edit='hero.title' data-type='text'>Welcome</h1>"
                "</section>"
            ),
        )

    def test_creates_tenant_account_without_request(self):
        tenant, owner, password = create_tenant_account(
            name="Acme",
            subdomain="acme",
            custom_domain="",
            template=self.template,
            username="acme-owner",
            email="owner@example.com",
        )

        self.assertEqual(tenant.owner, owner)
        self.assertEqual(tenant.content, self.template.schema["defaults"])
        self.assertTrue(tenant.is_published)
        self.assertTrue(owner.check_password(password))
        self.assertFalse(owner.is_staff)
        self.assertTrue(
            TenantMembership.objects.filter(
                tenant=tenant,
                user=owner,
                role=TenantMembership.ROLE_OWNER,
            ).exists()
        )

    def test_creates_scoped_login_without_request(self):
        tenant, _, _ = create_tenant_account(
            name="Acme",
            subdomain="acme",
            custom_domain="",
            template=self.template,
            username="acme-owner",
            email="owner@example.com",
        )

        user, password, errors = create_scoped_login(
            tenant,
            username="acme-editor",
            email="editor@example.com",
            role=TenantMembership.ROLE_EDITOR,
        )

        self.assertEqual(errors, [])
        self.assertTrue(user.check_password(password))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(
            TenantMembership.objects.filter(
                tenant=tenant,
                user=user,
                role=TenantMembership.ROLE_EDITOR,
            ).exists()
        )
