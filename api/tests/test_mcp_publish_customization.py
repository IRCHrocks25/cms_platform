"""The publish gate must not call a redesigned site untouched.

``_content_still_template_defaults`` stripped every ``_``-prefixed namespace
before comparing, so a site customized only through ``_styles`` (colors, fonts,
sizes), ``_hidden`` (sections switched off), or ``_tokens`` (brand colors) was
classified as boilerplate and refused.

Checked against how comparable products behave (WordPress.com, Webflow,
Squarespace, Wix, Ghost, Framer, Duda): none documents a "content must differ
from the template" publish gate, and where they track customization at all, a
design change is a change. Squarespace comes closest to our intent; it urges
replacing demo content and says you are not licensed to publish its samples,
but warns rather than blocks.

So: a design-only edit publishes, and carries a warning that the copy is still
the template's. A site with nothing changed at all is still refused, because
that is the case the gate exists for.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from api.auth import ResolvedAuth
from api.mcp.tools import publish_site
from core.models import Template, Tenant

User = get_user_model()

HTML = """
<section data-section="hero" data-label="Hero">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>
  <p data-edit="hero.lede" data-type="richtext" data-label="Lede">Some copy.</p>
</section>
"""


@override_settings(TENANT_BASE_DOMAIN="localhost")
class PublishCustomizationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("root", "r@ex.com", "x")
        self.auth = ResolvedAuth(
            user=self.admin, platform_role="superadmin", tenant_scopes=()
        )
        self.tpl = Template.objects.create(name="hero", html_source=HTML)
        self.tenant = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=self.tpl,
            owner=self.admin,
            content={},
            is_published=False,
        )

    def _publish(self, **args):
        return publish_site(self.auth, site="alpha", **args)

    def _set_content(self, content):
        self.tenant.content = content
        self.tenant.save(update_fields=["content", "updated_at"])

    def _text(self, result):
        return " ".join(
            c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)
        )

    def test_nothing_changed_is_still_refused(self):
        """The case the gate exists for."""
        result = self._publish()
        self.assertTrue(result["isError"])
        self.assertIn("force", self._text(result).lower())
        self.assertFalse(Tenant.objects.get(subdomain="alpha").is_published)

    def test_a_styling_only_edit_publishes(self):
        self._set_content({"_styles": {"hero.title": {"color": "#c4714b"}}})
        result = self._publish()
        self.assertFalse(result.get("isError", False), result)
        self.assertTrue(Tenant.objects.get(subdomain="alpha").is_published)

    def test_a_styling_only_edit_is_warned_that_the_copy_is_untouched(self):
        self._set_content({"_styles": {"hero.title": {"color": "#c4714b"}}})
        self.assertIn("copy", self._text(self._publish()).lower())

    def test_hiding_a_section_counts_as_a_change(self):
        self._set_content({"_hidden": ["hero.lede"]})
        self.assertFalse(self._publish().get("isError", False))

    def test_a_brand_token_change_counts_as_a_change(self):
        self._set_content({"_tokens": {"--primary": "#c4714b"}})
        self.assertFalse(self._publish().get("isError", False))

    def test_a_global_typography_change_counts_as_a_change(self):
        self._set_content({"_global": {"fontFamily": "DM Sans"}})
        self.assertFalse(self._publish().get("isError", False))

    def test_empty_meta_namespaces_do_not_count(self):
        """An editor that posts empty containers must not defeat the gate."""
        self._set_content({"_styles": {}, "_hidden": [], "_tokens": {}})
        self.assertTrue(self._publish()["isError"])

    def test_edited_copy_publishes_with_no_warning(self):
        self._set_content({"hero": {"title": "Kieran Haughey"}})
        result = self._publish()
        self.assertFalse(result.get("isError", False), result)
        self.assertNotIn("still the template", self._text(result).lower())

    def test_a_legacy_fat_row_is_still_recognised_as_untouched(self):
        """Rows written before sparse content hold a full copy of the defaults."""
        from core.renderer import merge_with_defaults

        self._set_content(merge_with_defaults(self.tpl.schema, {}))
        self.assertTrue(self._publish()["isError"])
