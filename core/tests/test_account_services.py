from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import CustomDomain, Template, Tenant, TenantMembership
from core.services.accounts import (
    AccountSeedError,
    CustomDomainError,
    create_scoped_login,
    create_tenant_account,
)


User = get_user_model()

RAW_HTML = "<html><body><h1>Hello from HTML</h1></body></html>"


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
        # Content starts empty, not as a copy of the defaults. A stored copy
        # would win over the template forever. See
        # core.tests.test_sparse_content_overrides.
        self.assertEqual(tenant.content, {})
        # New sites default to draft — the operator/client publishes on purpose
        # once content is ready (A6).
        self.assertFalse(tenant.is_published)
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


class CreateFromHtmlTests(TestCase):
    """CMS-38: html as an alternative seed to a library template."""

    @classmethod
    def setUpTestData(cls):
        cls.template = Template.objects.create(
            name="Library seed",
            html_source=(
                "<section data-section='hero' data-label='Hero'>"
                "<h1 data-edit='hero.title' data-type='text'>Welcome</h1>"
                "</section>"
            ),
        )

    def test_html_alone_creates_tenant_owned_template_with_that_html(self):
        tenant, owner, _password = create_tenant_account(
            name="Html Co",
            subdomain="htmlco",
            custom_domain="",
            username="htmlco-owner",
            email="owner@htmlco.test",
            html=RAW_HTML,
            is_published=False,
        )

        self.assertFalse(tenant.is_published)
        self.assertEqual(tenant.owner, owner)
        tpl = tenant.template
        self.assertIsNotNone(tpl)
        self.assertEqual(tpl.tenant_id, tenant.pk)
        self.assertIsNone(tpl.cloned_from_id)
        self.assertEqual(tpl.html_source, RAW_HTML)
        self.assertEqual(tpl.name, "Html Co")
        self.assertTrue(tpl.slug)
        self.assertEqual(tpl.editing_mode, Template.EDITING_RAW)
        self.assertEqual((tpl.schema or {}).get("sections") or [], [])

    def test_template_alone_still_clones_library_template(self):
        tenant, _, _ = create_tenant_account(
            name="Lib Co",
            subdomain="libco",
            custom_domain="",
            template=self.template,
            username="libco-owner",
            email="owner@libco.test",
            is_published=False,
        )
        self.assertNotEqual(tenant.template_id, self.template.pk)
        self.assertEqual(tenant.template.cloned_from_id, self.template.pk)
        self.assertEqual(tenant.template.tenant_id, tenant.pk)

    def test_both_template_and_html_refused(self):
        with self.assertRaises(AccountSeedError):
            create_tenant_account(
                name="Both Co",
                subdomain="bothco",
                custom_domain="",
                template=self.template,
                username="bothco-owner",
                email="owner@bothco.test",
                html=RAW_HTML,
            )
        self.assertFalse(Tenant.objects.filter(subdomain="bothco").exists())
        self.assertFalse(User.objects.filter(username="bothco-owner").exists())

    def test_template_and_empty_html_refused(self):
        with self.assertRaises(AccountSeedError):
            create_tenant_account(
                name="Empty Html Co",
                subdomain="emptyhtmlco",
                custom_domain="",
                template=self.template,
                username="emptyhtml-owner",
                email="owner@emptyhtmlco.test",
                html="",
            )

    def test_template_and_whitespace_html_refused(self):
        with self.assertRaises(AccountSeedError):
            create_tenant_account(
                name="Whitespace Html Co",
                subdomain="whitespacehtmlco",
                custom_domain="",
                template=self.template,
                username="whitespacehtml-owner",
                email="owner@whitespacehtmlco.test",
                html="   ",
            )

    def test_new_template_and_whitespace_html_refused(self):
        with self.assertRaises(AccountSeedError):
            create_tenant_account(
                name="Inline Whitespace Co",
                subdomain="inlinewhitespaceco",
                custom_domain="",
                username="inlinewhitespace-owner",
                email="owner@inlinewhitespaceco.test",
                new_template={
                    "name": "Inline seed",
                    "html_source": RAW_HTML,
                },
                html="   ",
            )

    def test_neither_template_nor_html_refused(self):
        with self.assertRaises(AccountSeedError):
            create_tenant_account(
                name="Neither Co",
                subdomain="neitherco",
                custom_domain="",
                username="neitherco-owner",
                email="owner@neitherco.test",
            )
        self.assertFalse(Tenant.objects.filter(subdomain="neitherco").exists())
        self.assertFalse(User.objects.filter(username="neitherco-owner").exists())

    def test_unannotated_html_yields_empty_schema_without_blowing_up(self):
        tenant, _, _ = create_tenant_account(
            name="Plain Co",
            subdomain="plainco",
            custom_domain="",
            username="plainco-owner",
            email="owner@plainco.test",
            html="<div>no data-edit here</div>",
        )
        self.assertEqual((tenant.template.schema or {}).get("sections") or [], [])
        self.assertEqual(tenant.content, {})


class CustomDomainAtCreationTests(TestCase):
    """CMS-37: custom_domain passed at creation now produces a real,
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
