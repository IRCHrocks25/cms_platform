"""CMS-27 §7 / §11 — ownership backfill claims single-owner templates only."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


User = get_user_model()

ANNOTATED = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Hi</h1>
</section>
"""
RAW = "<html><body><p>static</p></body></html>"


class TemplateOwnershipBackfillTests(TransactionTestCase):
    """Run the data migration against a prod-shaped fixture."""

    app = "core"
    migrate_from = [("core", "0021_template_ownership_and_versions")]
    migrate_to = [("core", "0022_backfill_template_ownership")]

    def _migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_backfill_claims_single_owner_keeps_shared_library(self):
        apps = self._migrate(self.migrate_from)
        Template = apps.get_model("core", "Template")
        Tenant = apps.get_model("core", "Tenant")
        Page = apps.get_model("core", "Page")
        UserModel = apps.get_model("auth", "User")

        owner = UserModel.objects.create_user(
            username="own", email="o@ex.com", password="x"
        )
        other = UserModel.objects.create_user(
            username="oth", email="t@ex.com", password="x"
        )

        shared = Template.objects.create(
            name="AI-Consultant",
            slug="ai-consultant",
            html_source=ANNOTATED,
            schema={"sections": [{"id": "hero"}], "defaults": {}},
            editing_mode="raw",
        )
        home_only = Template.objects.create(
            name="Home Only",
            slug="home-only",
            html_source=ANNOTATED,
            schema={"sections": [{"id": "hero"}], "defaults": {}},
            editing_mode="raw",
        )
        page_only = Template.objects.create(
            name="Page Only",
            slug="page-only",
            html_source=ANNOTATED,
            schema={"sections": [{"id": "hero"}], "defaults": {}},
            editing_mode="raw",
        )
        orphan = Template.objects.create(
            name="Orphan",
            slug="orphan",
            html_source=RAW,
            schema={"sections": [], "defaults": {}},
            editing_mode="raw",
        )
        unannotated_live = Template.objects.create(
            name="Stephanie Yee Website",
            slug="stephanie",
            html_source=RAW,
            schema={"sections": [], "defaults": {}},
            editing_mode="raw",
        )

        t1 = Tenant.objects.create(
            name="Site1",
            subdomain="site1",
            template=shared,
            owner=owner,
            content={},
            is_published=True,
        )
        t2 = Tenant.objects.create(
            name="Site2",
            subdomain="site2",
            template=shared,
            owner=other,
            content={},
            is_published=True,
        )
        t3 = Tenant.objects.create(
            name="Site3",
            subdomain="site3",
            template=home_only,
            owner=owner,
            content={},
            is_published=True,
        )
        t4 = Tenant.objects.create(
            name="Site4",
            subdomain="site4",
            template=unannotated_live,
            owner=other,
            content={},
            is_published=True,
        )
        Page.objects.create(
            tenant=t3,
            template=page_only,
            title="About",
            slug="about",
            content={},
            is_published=True,
        )

        before_with_sections = sum(
            1
            for tpl in Template.objects.all()
            if (tpl.schema or {}).get("sections")
        )
        before_sites_with_sections = sum(
            1
            for ten in Tenant.objects.select_related("template")
            if (ten.template.schema or {}).get("sections")
        )

        apps = self._migrate(self.migrate_to)
        Template = apps.get_model("core", "Template")
        Tenant = apps.get_model("core", "Tenant")
        TemplateVersion = apps.get_model("core", "TemplateVersion")

        shared = Template.objects.get(slug="ai-consultant")
        home_only = Template.objects.get(slug="home-only")
        page_only = Template.objects.get(slug="page-only")
        orphan = Template.objects.get(slug="orphan")
        unannotated_live = Template.objects.get(slug="stephanie")

        self.assertIsNone(shared.tenant_id)
        self.assertEqual(home_only.tenant_id, t3.id)
        self.assertEqual(page_only.tenant_id, t3.id)
        self.assertIsNone(orphan.tenant_id)
        self.assertEqual(unannotated_live.tenant_id, t4.id)

        self.assertEqual(shared.editing_mode, "editable")
        self.assertEqual(home_only.editing_mode, "editable")
        self.assertEqual(orphan.editing_mode, "raw")
        self.assertEqual(unannotated_live.editing_mode, "raw")

        after_with_sections = sum(
            1
            for tpl in Template.objects.all()
            if (tpl.schema or {}).get("sections")
        )
        self.assertEqual(after_with_sections, before_with_sections)

        after_sites_with_sections = sum(
            1
            for ten in Tenant.objects.select_related("template")
            if (ten.template.schema or {}).get("sections")
        )
        self.assertEqual(after_sites_with_sections, before_sites_with_sections)

        # Behavioural no-op for client editing: editable iff sections present.
        for tpl in Template.objects.all():
            has = bool((tpl.schema or {}).get("sections"))
            self.assertEqual(
                tpl.editing_mode == "editable",
                has,
                msg=f"template {tpl.pk} editing_mode mismatch",
            )

        self.assertEqual(TemplateVersion.objects.count(), Template.objects.count())
        self.assertTrue(
            TemplateVersion.objects.filter(template=shared, number=1).exists()
        )
        # Shared template still points at both tenants' home pages.
        self.assertEqual(
            Tenant.objects.filter(template_id=shared.pk).count(),
            2,
        )
