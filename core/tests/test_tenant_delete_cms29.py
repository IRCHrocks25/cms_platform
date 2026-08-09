"""CMS-29 — deleting a tenant must not collide on uniq_library_template_slug.

create_client_account / create_tenant_account clone a library template into
the new tenant with the same slug (legal: uniqueness is per-owner). On delete,
Template.tenant SET_NULL promotes those clones into the library; without a
re-slug they hit the library-only unique index.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Page, Template, TemplateVersion, Tenant
from core.services.accounts import create_tenant_account


User = get_user_model()

HTML = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Hi</h1>
</section>
"""


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TenantDeleteClonedTemplateTests(TestCase):
    """Acceptance: create via create_client_account path → delete succeeds."""

    def setUp(self):
        self.staff = User.objects.create_user(
            "agency_cms29", password="x", is_staff=True
        )
        self.library = Template.objects.create(
            name="Test Template",
            html_source=HTML,
            editing_mode=Template.EDITING_EDITABLE,
        )

    def test_delete_tenant_with_library_clone_succeeds(self):
        tenant, _user, _pw = create_tenant_account(
            name="Doomed",
            subdomain="doomed-cms29",
            custom_domain="",
            template=self.library,
            username="doomed_cms29",
            email="doomed@example.com",
            is_published=False,
        )
        clone = tenant.template
        self.assertEqual(clone.slug, self.library.slug)
        self.assertEqual(clone.tenant_id, tenant.pk)
        version_ids = list(
            TemplateVersion.objects.filter(template=clone).values_list("pk", flat=True)
        )
        self.assertTrue(version_ids)

        tenant_pk = tenant.pk
        clone_pk = clone.pk
        # Must not raise IntegrityError (the CMS-29 failure mode).
        tenant.delete()

        self.assertFalse(Tenant.objects.filter(pk=tenant_pk).exists())
        promoted = Template.objects.get(pk=clone_pk)
        self.assertIsNone(promoted.tenant_id)
        self.assertNotEqual(promoted.slug, self.library.slug)
        self.assertTrue(
            TemplateVersion.objects.filter(pk__in=version_ids, template=promoted).exists()
        )
        self.assertEqual(
            TemplateVersion.objects.filter(template_id=clone_pk).count(),
            len(version_ids),
        )

    def test_dashboard_delete_cloned_site_succeeds(self):
        tenant, _user, _pw = create_tenant_account(
            name="Dash Delete",
            subdomain="dash-cms29",
            custom_domain="",
            template=self.library,
            username="dash_cms29",
            email="dash@example.com",
            is_published=False,
        )
        clone_pk = tenant.template_id
        version_count = TemplateVersion.objects.filter(template_id=clone_pk).count()

        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        response = c.post(
            reverse("dashboard:tenant_delete", args=[tenant.pk]),
            data={"confirm_subdomain": "dash-cms29"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Tenant.objects.filter(pk=tenant.pk).exists())
        promoted = Template.objects.get(pk=clone_pk)
        self.assertIsNone(promoted.tenant_id)
        self.assertEqual(
            TemplateVersion.objects.filter(template_id=clone_pk).count(),
            version_count,
        )

    def test_delete_tenant_with_pages_and_multiple_owned_templates(self):
        tenant, _user, _pw = create_tenant_account(
            name="Multi",
            subdomain="multi-cms29",
            custom_domain="",
            template=self.library,
            username="multi_cms29",
            email="multi@example.com",
            is_published=False,
        )
        home_clone = tenant.template
        page_tpl = Template.objects.create(
            name="Test Template",  # same name → same base slug under this tenant
            html_source=HTML,
            tenant=tenant,
            editing_mode=Template.EDITING_EDITABLE,
        )
        TemplateVersion.objects.create(
            template=page_tpl,
            number=1,
            html_source=page_tpl.html_source,
            schema=page_tpl.schema or {},
            label="Initial",
        )
        Page.objects.create(
            tenant=tenant,
            template=page_tpl,
            title="About",
            slug="about",
            content={},
            is_published=True,
        )
        # Extra owned template not attached to a page (push-style leftover).
        orphan_owned = Template.objects.create(
            name="Test Template Extra",
            html_source=HTML,
            tenant=tenant,
        )
        TemplateVersion.objects.create(
            template=orphan_owned,
            number=1,
            html_source=orphan_owned.html_source,
            schema=orphan_owned.schema or {},
            label="Initial",
        )

        home_versions = TemplateVersion.objects.filter(template=home_clone).count()
        page_versions = TemplateVersion.objects.filter(template=page_tpl).count()
        orphan_versions = TemplateVersion.objects.filter(template=orphan_owned).count()
        home_pk, page_pk, orphan_pk = home_clone.pk, page_tpl.pk, orphan_owned.pk
        tenant_pk = tenant.pk

        tenant.delete()

        self.assertFalse(Tenant.objects.filter(pk=tenant_pk).exists())
        self.assertFalse(Page.objects.filter(tenant_id=tenant_pk).exists())
        for pk, n in (
            (home_pk, home_versions),
            (page_pk, page_versions),
            (orphan_pk, orphan_versions),
        ):
            tpl = Template.objects.get(pk=pk)
            self.assertIsNone(tpl.tenant_id)
            self.assertEqual(TemplateVersion.objects.filter(template_id=pk).count(), n)

        # All three now live in the library; library slugs must be unique.
        slugs = list(
            Template.objects.filter(pk__in=[home_pk, page_pk, orphan_pk]).values_list(
                "slug", flat=True
            )
        )
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertNotIn(self.library.slug, slugs)  # home clone was re-slugged


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TenantDeleteFriendlyErrorTests(TestCase):
    """A blocked delete must explain itself rather than raise IntegrityError."""

    def setUp(self):
        self.staff = User.objects.create_user(
            "agency_err", password="x", is_staff=True
        )
        self.library = Template.objects.create(name="Lib Err", html_source=HTML)

    def test_view_does_not_500_on_integrity_error(self):
        tenant, _user, _pw = create_tenant_account(
            name="Fragile",
            subdomain="fragile-cms29",
            custom_domain="",
            template=self.library,
            username="fragile_cms29",
            email="fragile@example.com",
            is_published=False,
        )
        # Simulate the pre-fix failure mode: delete without re-slug prep.
        # Force a collision by monkeypatching the prep hook if present; otherwise
        # call the raw collector path. Prefer asserting the view catches errors.
        from unittest.mock import patch

        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)

        def boom(*args, **kwargs):
            raise IntegrityError('duplicate key value violates unique constraint "uniq_library_template_slug"')

        with patch.object(Tenant, "delete", boom):
            response = c.post(
                reverse("dashboard:tenant_delete", args=[tenant.pk]),
                data={"confirm_subdomain": "fragile-cms29"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Tenant.objects.filter(pk=tenant.pk).exists())
        # Landed back on detail with a flash, not a 500.
        self.assertIn(
            reverse("dashboard:tenant_detail", args=[tenant.pk]),
            response["Location"],
        )
