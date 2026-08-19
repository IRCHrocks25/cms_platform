"""CMS-27: Template ownership, editing_mode matrix, slug constraints, versions."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from core.models import Template, Tenant, TemplateVersion


User = get_user_model()

ANNOTATED_HTML = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>
</section>
"""

RAW_HTML = "<html><body><h1>Static</h1></body></html>"


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TemplateEditingModeTests(TestCase):
    def test_is_client_editable_matrix(self):
        cases = [
            ("raw", False, "raw", False),
            ("editable", False, "annotation_pending", False),
            ("raw", True, "annotated_not_released", False),
            ("editable", True, "editable", True),
        ]
        for editing_mode, has_sections, status, editable in cases:
            with self.subTest(editing_mode=editing_mode, has_sections=has_sections):
                tpl = Template.objects.create(
                    name=f"{editing_mode}-{has_sections}",
                    html_source=ANNOTATED_HTML if has_sections else RAW_HTML,
                    editing_mode=editing_mode,
                )
                self.assertEqual(tpl.has_editable_schema, has_sections)
                self.assertEqual(tpl.annotation_status, status)
                self.assertEqual(tpl.is_client_editable, editable)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TemplateSlugConstraintTests(TestCase):
    def setUp(self):
        self.owner_a = User.objects.create_user("a", "a@ex.com", "x")
        self.owner_b = User.objects.create_user("b", "b@ex.com", "x")
        self.lib = Template.objects.create(name="Lib Base", html_source=RAW_HTML)
        self.tenant_a = Tenant.objects.create(
            name="A",
            subdomain="a",
            template=self.lib,
            owner=self.owner_a,
        )
        self.tenant_b = Tenant.objects.create(
            name="B",
            subdomain="b",
            template=self.lib,
            owner=self.owner_b,
        )

    def test_two_tenants_can_share_slug(self):
        Template.objects.create(
            name="Acme",
            slug="acme",
            html_source=RAW_HTML,
            tenant=self.tenant_a,
        )
        Template.objects.create(
            name="Acme",
            slug="acme",
            html_source=RAW_HTML,
            tenant=self.tenant_b,
        )
        self.assertEqual(
            Template.objects.filter(slug="acme", tenant__isnull=False).count(),
            2,
        )

    def test_two_library_templates_cannot_share_slug(self):
        Template.objects.create(
            name="Shared",
            slug="shared-lib",
            html_source=RAW_HTML,
            tenant=None,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Template.objects.create(
                    name="Shared 2",
                    slug="shared-lib",
                    html_source=RAW_HTML,
                    tenant=None,
                )


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TemplateVersionModelTests(TestCase):
    def test_version_unique_per_template_number(self):
        tpl = Template.objects.create(name="V", html_source=RAW_HTML)
        TemplateVersion.objects.create(
            template=tpl,
            number=1,
            html_source=tpl.html_source,
            schema=tpl.schema,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TemplateVersion.objects.create(
                    template=tpl,
                    number=1,
                    html_source=tpl.html_source,
                    schema=tpl.schema,
                )
