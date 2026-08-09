from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import CustomDomain, Template, Tenant, TenantMembership
from core.services.accounts import (
    CustomDomainError,
    create_scoped_login,
    create_tenant_account,
)


User = get_user_model()


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

    def test_creates_tenant_account_unpublished_when_requested(self):
        tenant, _, _ = create_tenant_account(
            name="Draft Co",
            subdomain="draftco",
            custom_domain="",
            template=self.template,
            username="draft-owner",
            email="draft@example.com",
            is_published=False,
        )
        self.assertFalse(tenant.is_published)

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


class CustomDomainAtCreationTests(TestCase):
    """CMS-37 — custom_domain passed at creation now produces a real,
    unverified CustomDomain row instead of a write that nothing reads."""

    @classmethod
    def setUpTestData(cls):
        cls.template = Template.objects.create(
            name="Domain template",
            html_source=(
                "<section data-section='hero' data-label='Hero'>"
                "<h1 data-edit='hero.title' data-type='text'>Welcome</h1>"
                "</section>"
            ),
        )

    def test_valid_custom_domain_creates_unverified_row_linked_to_tenant(self):
        tenant, _owner, _password = create_tenant_account(
            name="Acme",
            subdomain="acme-domain",
            custom_domain="www.acme.com",
            template=self.template,
            username="acme-domain-owner",
            email="owner@example.com",
        )

        row = CustomDomain.objects.get(domain="www.acme.com")
        self.assertEqual(row.tenant_id, tenant.pk)
        self.assertFalse(row.is_verified)

    def test_no_custom_domain_creates_no_row_and_still_works(self):
        tenant, owner, _password = create_tenant_account(
            name="No Domain Co",
            subdomain="no-domain-co",
            custom_domain="",
            template=self.template,
            username="no-domain-owner",
            email="owner@example.com",
        )

        self.assertEqual(tenant.owner, owner)
        self.assertFalse(CustomDomain.objects.exists())

    def test_invalid_custom_domain_leaves_no_tenant_or_user_behind(self):
        with self.assertRaises(CustomDomainError):
            create_tenant_account(
                name="Bad Domain Co",
                subdomain="bad-domain-co",
                custom_domain="not a domain",
                template=self.template,
                username="bad-domain-owner",
                email="owner@example.com",
            )

        self.assertFalse(Tenant.objects.filter(subdomain="bad-domain-co").exists())
        self.assertFalse(User.objects.filter(username="bad-domain-owner").exists())
        self.assertFalse(CustomDomain.objects.exists())

    def test_domain_already_registered_to_another_tenant_is_refused(self):
        other_owner = User.objects.create_user("other-owner", password="x")
        other_tenant = Tenant.objects.create(
            name="Other",
            subdomain="other-tenant",
            template=self.template,
            owner=other_owner,
        )
        CustomDomain.objects.create(
            tenant=other_tenant, domain="taken.com", is_verified=True
        )

        with self.assertRaises(CustomDomainError):
            create_tenant_account(
                name="Late Co",
                subdomain="late-co",
                custom_domain="taken.com",
                template=self.template,
                username="late-owner",
                email="owner@example.com",
            )

        self.assertFalse(Tenant.objects.filter(subdomain="late-co").exists())
        self.assertFalse(User.objects.filter(username="late-owner").exists())
        self.assertEqual(
            CustomDomain.objects.filter(domain="taken.com").count(), 1
        )
        self.assertEqual(
            CustomDomain.objects.get(domain="taken.com").tenant_id, other_tenant.pk
        )

