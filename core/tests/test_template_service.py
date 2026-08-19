"""CMS-27 §6 / §11: template write service guards and versioning."""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import Page, Template, TemplateVersion, Tenant
from core.services import templates as tpl_svc


User = get_user_model()

HTML_A = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">A</h1>
  <p data-edit="hero.sub" data-type="text" data-label="Sub">Sub</p>
</section>
"""

HTML_B = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">B</h1>
</section>
"""

RAW = "<html><body><p>x</p></body></html>"

HTML_EMBED_DEFAULT = """
<section data-section="contact" data-label="Contact">
  <div data-edit="contact.embed" data-type="ghl-embed"
       data-ghl-kind="form">form:archived_form</div>
</section>
"""


@override_settings(TENANT_BASE_DOMAIN="localhost")
class AssignTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ops", "o@ex.com", "x", is_staff=True)
        self.owner_a = User.objects.create_user("a", "a@ex.com", "x")
        self.owner_b = User.objects.create_user("b", "b@ex.com", "x")
        self.lib = Template.objects.create(
            name="Lib",
            html_source=HTML_A,
            editing_mode=Template.EDITING_EDITABLE,
        )
        self.tenant_a = Tenant.objects.create(
            name="A",
            subdomain="a",
            template=self.lib,
            owner=self.owner_a,
            content={"hero": {"title": "kept", "sub": "s"}},
        )
        self.owned = Template.objects.create(
            name="Owned A",
            html_source=HTML_A,
            tenant=self.tenant_a,
            editing_mode=Template.EDITING_EDITABLE,
        )
        self.tenant_a.template = self.owned
        self.tenant_a.save(update_fields=["template"])
        self.tenant_b = Tenant.objects.create(
            name="B",
            subdomain="b",
            template=self.lib,
            owner=self.owner_b,
            content={},
        )

    def test_cross_tenant_assign_raises(self):
        with self.assertRaises(tpl_svc.CrossTenantTemplateError) as ctx:
            tpl_svc.assign_template(self.tenant_b, self.owned, user=self.user)
        self.assertIn("duplicate", str(ctx.exception).lower())
        self.tenant_b.refresh_from_db()
        self.assertEqual(self.tenant_b.template_id, self.lib.pk)

    def test_library_assign_clones(self):
        result = tpl_svc.assign_template(self.tenant_b, self.lib, user=self.user)
        self.tenant_b.refresh_from_db()
        self.assertEqual(self.tenant_b.template_id, result.pk)
        self.assertNotEqual(result.pk, self.lib.pk)
        self.assertEqual(result.cloned_from_id, self.lib.pk)
        self.assertEqual(result.tenant_id, self.tenant_b.pk)
        self.assertEqual(result.versions.count(), 1)
        self.assertEqual(result.versions.first().number, 1)

    def test_same_tenant_assign_no_clone(self):
        other = Template.objects.create(
            name="Also A",
            html_source=HTML_B,
            tenant=self.tenant_a,
            editing_mode=Template.EDITING_EDITABLE,
        )
        before = Template.objects.count()
        result = tpl_svc.assign_template(self.tenant_a, other, user=self.user)
        self.assertEqual(result.pk, other.pk)
        self.assertEqual(Template.objects.count(), before)
        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.template_id, other.pk)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class FieldLossGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ops", "o@ex.com", "x", is_staff=True)
        self.owner = User.objects.create_user("own", "own@ex.com", "x")
        self.tpl = Template.objects.create(
            name="T",
            html_source=HTML_A,
            editing_mode=Template.EDITING_EDITABLE,
        )
        TemplateVersion.objects.create(
            template=self.tpl,
            number=1,
            html_source=self.tpl.html_source,
            schema=self.tpl.schema,
        )
        self.tenant = Tenant.objects.create(
            name="Live",
            subdomain="live",
            template=self.tpl,
            owner=self.owner,
            content={"hero": {"title": "Live title", "sub": "Live sub"}},
            is_published=True,
        )
        self.tpl.tenant = self.tenant
        self.tpl.save(update_fields=["tenant"])

    def test_published_field_loss_blocked_without_flag(self):
        with self.assertRaises(tpl_svc.FieldLossError) as ctx:
            tpl_svc.save_template_version(
                self.tpl, HTML_B, user=self.user, allow_field_loss=False
            )
        self.assertIn("hero.sub", ctx.exception.lost_fields)
        self.tpl.refresh_from_db()
        self.assertIn("hero.sub", _field_ids(self.tpl.schema))
        self.assertEqual(self.tpl.versions.count(), 1)

    def test_published_field_loss_allowed_with_flag_preserves_content(self):
        result = tpl_svc.save_template_version(
            self.tpl, HTML_B, user=self.user, allow_field_loss=True
        )
        self.tpl.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertNotIn("hero.sub", _field_ids(self.tpl.schema))
        self.assertEqual(self.tenant.content["hero"]["sub"], "Live sub")
        self.assertEqual(self.tpl.versions.count(), 2)
        self.assertEqual(result.lost_fields, {"hero.sub"})

    def test_unpublished_field_loss_does_not_block(self):
        self.tenant.is_published = False
        self.tenant.save(update_fields=["is_published"])
        result = tpl_svc.save_template_version(
            self.tpl, HTML_B, user=self.user, allow_field_loss=False
        )
        self.assertEqual(result.lost_fields, {"hero.sub"})
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.versions.count(), 2)

    def test_page_published_field_loss_blocked(self):
        page_tpl = Template.objects.create(
            name="Page T",
            html_source=HTML_A,
            tenant=self.tenant,
            editing_mode=Template.EDITING_EDITABLE,
        )
        TemplateVersion.objects.create(
            template=page_tpl,
            number=1,
            html_source=page_tpl.html_source,
            schema=page_tpl.schema,
        )
        Page.objects.create(
            tenant=self.tenant,
            template=page_tpl,
            title="About",
            slug="about",
            content={"hero": {"title": "P", "sub": "page sub"}},
            is_published=True,
        )
        with self.assertRaises(tpl_svc.FieldLossError):
            tpl_svc.save_template_version(
                page_tpl, HTML_B, user=self.user, allow_field_loss=False
            )


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TemplateVersionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ops", "o@ex.com", "x", is_staff=True)
        self.tpl = Template.objects.create(name="V", html_source=HTML_A)

    def test_save_appends_contiguous_numbers(self):
        tpl_svc.save_template_version(self.tpl, HTML_A, user=self.user, label="v1")
        tpl_svc.save_template_version(self.tpl, HTML_B, user=self.user, label="v2")
        numbers = list(
            self.tpl.versions.order_by("number").values_list("number", flat=True)
        )
        self.assertEqual(numbers, [1, 2])

    def test_restore_appends_rather_than_deletes(self):
        tpl_svc.save_template_version(self.tpl, HTML_A, user=self.user)
        tpl_svc.save_template_version(self.tpl, HTML_B, user=self.user)
        v1 = self.tpl.versions.get(number=1)
        tpl_svc.restore_template_version(self.tpl, v1, user=self.user)
        self.assertEqual(self.tpl.versions.count(), 3)
        self.tpl.refresh_from_db()
        self.assertIn("hero.sub", _field_ids(self.tpl.schema))

    def test_restore_rederives_schema_and_logs_parser_drift(self):
        tpl_svc.save_template_version(self.tpl, HTML_A, user=self.user)
        v1 = self.tpl.versions.get(number=1)
        # Poison archived schema so a re-derive differs.
        v1.schema = {"sections": [{"id": "stale"}], "defaults": {}}
        v1.save(update_fields=["schema"])

        with self.assertLogs("core.services.templates", level=logging.WARNING) as cm:
            tpl_svc.restore_template_version(self.tpl, v1, user=self.user)
        self.assertTrue(any("parser.py has changed" in m for m in cm.output))
        self.tpl.refresh_from_db()
        self.assertNotEqual(self.tpl.schema.get("sections"), v1.schema.get("sections"))
        self.assertIn("hero", {s["id"] for s in self.tpl.schema.get("sections", [])})

    def test_restore_refuses_archived_populated_embed_default(self):
        version = TemplateVersion.objects.create(
            template=self.tpl,
            number=1,
            html_source=HTML_EMBED_DEFAULT,
            schema={"sections": [], "defaults": {}},
            saved_by=self.user,
        )
        original_html = self.tpl.html_source

        with self.assertRaisesRegex(ValueError, "must be empty"):
            tpl_svc.restore_template_version(self.tpl, version, user=self.user)

        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.html_source, original_html)
        self.assertEqual(self.tpl.versions.count(), 1)


def _field_ids(schema: dict) -> set[str]:
    ids: set[str] = set()
    for section in (schema or {}).get("sections") or []:
        sid = section.get("id")
        for field in section.get("fields") or []:
            fid = field.get("id")
            if sid and fid:
                ids.add(f"{sid}.{fid}" if "." not in fid else fid)
            elif fid:
                ids.add(fid)
    return ids
