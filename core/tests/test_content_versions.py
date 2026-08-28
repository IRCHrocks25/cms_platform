"""CMS-7: shared snapshot-then-save + MCP retention isolation."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import ContentVersion, Template, Tenant
from core.services import content_versions as cv


User = get_user_model()

SAMPLE_HTML = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>
</section>
"""

EMBED_HTML = """
<section data-section="contact" data-label="Contact">
  <div data-edit="contact.embed" data-type="ghl-embed"
       data-ghl-kind="form"></div>
</section>
"""


@override_settings(TENANT_BASE_DOMAIN="localhost")
class ContentVersionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("editor", "e@ex.com", "x")
        self.other = User.objects.create_user("other", "o@ex.com", "x")
        tpl = Template.objects.create(name="tpl", html_source=SAMPLE_HTML)
        self.tenant = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=tpl,
            owner=self.user,
            content={"hero": {"title": "A"}},
        )

    def test_dashboard_save_creates_snapshot_of_previous(self):
        before = dict(self.tenant.content)
        cv.save_tenant_content(
            self.tenant,
            {"hero": {"title": "B"}},
            user=self.user,
            source=cv.SOURCE_DASHBOARD,
        )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"hero": {"title": "B"}})
        versions = list(self.tenant.versions.order_by("-saved_at"))
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].snapshot, before)
        self.assertEqual(versions[0].source, cv.SOURCE_DASHBOARD)
        self.assertEqual(versions[0].saved_by_id, self.user.pk)

    def test_mcp_burst_coalesces_into_one_snapshot(self):
        """Rapid MCP patches must not create one version per field write."""
        cv.save_tenant_content(
            self.tenant,
            {"hero": {"title": "M1"}},
            user=self.user,
            source=cv.SOURCE_MCP,
        )
        cv.save_tenant_content(
            self.tenant,
            {"hero": {"title": "M2"}},
            user=self.user,
            source=cv.SOURCE_MCP,
        )
        cv.save_tenant_content(
            self.tenant,
            {"hero": {"title": "M3"}},
            user=self.user,
            source=cv.SOURCE_MCP,
        )
        self.assertEqual(self.tenant.versions.filter(source=cv.SOURCE_MCP).count(), 1)
        snap = self.tenant.versions.get(source=cv.SOURCE_MCP).snapshot
        self.assertEqual(snap, {"hero": {"title": "A"}})

    def test_mcp_versions_do_not_flush_dashboard_undo_history(self):
        """Separate retention: many MCP snapshots must not evict human ones.

        Fails under a single rolling-10 that mixes sources.
        """
        # Seed 10 dashboard (human) undo points.
        for i in range(10):
            cv.save_tenant_content(
                self.tenant,
                {"hero": {"title": f"H{i}"}},
                user=self.user,
                source=cv.SOURCE_DASHBOARD,
            )
        human_ids = set(
            self.tenant.versions.filter(source=cv.SOURCE_DASHBOARD).values_list(
                "id", flat=True
            )
        )
        self.assertEqual(len(human_ids), 10)

        # Force non-coalesced MCP saves (different users / outside window).
        for i in range(12):
            # Advance clock past coalesce window between MCP bursts.
            latest = self.tenant.versions.filter(source=cv.SOURCE_MCP).first()
            if latest is not None:
                ContentVersion.objects.filter(pk=latest.pk).update(
                    saved_at=timezone.now() - timedelta(minutes=30)
                )
            cv.save_tenant_content(
                self.tenant,
                {"hero": {"title": f"M{i}"}},
                user=self.user,
                source=cv.SOURCE_MCP,
            )

        remaining_human = set(
            self.tenant.versions.filter(source=cv.SOURCE_DASHBOARD).values_list(
                "id", flat=True
            )
        )
        self.assertEqual(remaining_human, human_ids)
        self.assertEqual(
            self.tenant.versions.filter(source=cv.SOURCE_DASHBOARD).count(), 10
        )
        self.assertLessEqual(
            self.tenant.versions.filter(source=cv.SOURCE_MCP).count(),
            cv.MCP_KEEP,
        )

    def test_restore_uses_shared_path(self):
        cv.save_tenant_content(
            self.tenant,
            {"hero": {"title": "B"}},
            user=self.user,
            source=cv.SOURCE_DASHBOARD,
        )
        version = self.tenant.versions.get()
        cv.restore_tenant_content(self.tenant, version, user=self.other)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"hero": {"title": "A"}})
        # Restore itself is undoable because current was snapshotted first.
        newest = self.tenant.versions.order_by("-saved_at").first()
        self.assertEqual(newest.snapshot, {"hero": {"title": "B"}})

    def test_pop_undo_walks_back_through_history(self):
        """Linear undo: repeated pop-restores step back A<-B<-C instead of
        toggling between the last two states (the pre-fix bug)."""
        cv.save_tenant_content(self.tenant, {"hero": {"title": "B"}}, user=self.user)
        cv.save_tenant_content(self.tenant, {"hero": {"title": "C"}}, user=self.user)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"hero": {"title": "C"}})

        def undo():
            newest = self.tenant.versions.order_by("-saved_at").first()
            cv.restore_tenant_content(self.tenant, newest, user=self.user, pop=True)
            self.tenant.refresh_from_db()

        undo()
        self.assertEqual(self.tenant.content, {"hero": {"title": "B"}})
        undo()
        self.assertEqual(self.tenant.content, {"hero": {"title": "A"}})
        # History is consumed as we walk back — no fresh redo points pile up.
        self.assertEqual(self.tenant.versions.count(), 0)

    def test_pop_undo_does_not_create_redo_point(self):
        cv.save_tenant_content(self.tenant, {"hero": {"title": "B"}}, user=self.user)
        version = self.tenant.versions.get()
        cv.restore_tenant_content(self.tenant, version, user=self.user, pop=True)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"hero": {"title": "A"}})
        # pop consumed the only snapshot and pushed nothing new.
        self.assertEqual(self.tenant.versions.count(), 0)

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_restore_refuses_deleted_or_foreign_embed_form(self, list_forms):
        template = self.tenant.template
        template.html_source = EMBED_HTML
        template.save()
        self.tenant.content = {"contact": {"embed": "form:current_form"}}
        self.tenant.is_published = True
        self.tenant.save(update_fields=["content", "is_published", "updated_at"])
        version = ContentVersion.objects.create(
            tenant=self.tenant,
            snapshot={"contact": {"embed": "form:deleted_or_foreign_form"}},
            saved_by=self.user,
            source=cv.SOURCE_DASHBOARD,
        )
        version_count = self.tenant.versions.count()
        list_forms.return_value = [{"id": "current_form", "name": "Current"}]

        with self.assertRaisesRegex(ValueError, "not available for this site"):
            cv.restore_tenant_content(self.tenant, version, user=self.other)

        self.tenant.refresh_from_db()
        self.assertEqual(
            self.tenant.content, {"contact": {"embed": "form:current_form"}}
        )
        self.assertEqual(self.tenant.versions.count(), version_count)
        list_forms.assert_called_once_with(self.tenant)
