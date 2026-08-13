from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from core.models import BlogPost, Page, Template, Tenant


User = get_user_model()


class SeedDemoDataCommandTests(TestCase):
    def test_refuses_without_explicit_opt_in(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesMessage(CommandError, "ALLOW_DEMO_SEED=1"):
                call_command("seed_demo_data")

    @patch.dict("os.environ", {"ALLOW_DEMO_SEED": "1"})
    def test_seed_is_idempotent_and_covers_ui_edge_cases(self):
        call_command("seed_demo_data", stdout=StringIO())
        first_counts = (
            Template.objects.count(),
            Tenant.objects.count(),
            Page.objects.count(),
            BlogPost.objects.count(),
            User.objects.count(),
        )

        call_command("seed_demo_data", stdout=StringIO())

        self.assertEqual(
            (
                Template.objects.count(),
                Tenant.objects.count(),
                Page.objects.count(),
                BlogPost.objects.count(),
                User.objects.count(),
            ),
            first_counts,
        )
        self.assertGreaterEqual(Template.objects.count(), 3)
        self.assertGreaterEqual(Tenant.objects.count(), 5)
        self.assertTrue(Tenant.objects.filter(name__startswith="[DEMO]").exists())
        self.assertTrue(Tenant.objects.filter(pages__isnull=True).exists())
        self.assertTrue(Tenant.objects.filter(blog_posts__isnull=True).exists())
        self.assertTrue(Page.objects.filter(is_published=False).exists())
        self.assertTrue(any(len(page.title) >= 100 for page in Page.objects.all()))
        self.assertTrue(
            any(
                len((template.schema or {}).get("sections") or []) >= 16
                for template in Template.objects.all()
            )
        )
        self.assertTrue(
            any(tenant.content == {} for tenant in Tenant.objects.all())
        )
        self.assertGreaterEqual(
            max(tenant.blog_posts.count() for tenant in Tenant.objects.all()),
            10,
        )

    @patch.dict("os.environ", {"ALLOW_DEMO_SEED": "1"})
    def test_clear_removes_exact_seed_manifest_and_preserves_other_rows(self):
        outsider = User.objects.create_user("demo-seed-outsider")
        outsider_template = Template.objects.create(
            name="[DEMO-ish] Hand-created template",
            slug="demo-seed-outsider",
            html_source="<main>Keep me</main>",
        )
        outsider_tenant = Tenant.objects.create(
            name="[DEMO-ish] Hand-created site",
            subdomain="demo-seed-outsider",
            template=outsider_template,
            owner=outsider,
        )
        call_command("seed_demo_data", stdout=StringIO())

        call_command("seed_demo_data", clear=True, stdout=StringIO())

        self.assertTrue(User.objects.filter(pk=outsider.pk).exists())
        self.assertTrue(Template.objects.filter(pk=outsider_template.pk).exists())
        self.assertTrue(Tenant.objects.filter(pk=outsider_tenant.pk).exists())
        self.assertFalse(Tenant.objects.filter(name__startswith="[DEMO]").exists())
        self.assertFalse(Template.objects.filter(name__startswith="[DEMO]").exists())
        self.assertFalse(User.objects.filter(username__startswith="demo-seed-").exclude(pk=outsider.pk).exists())
