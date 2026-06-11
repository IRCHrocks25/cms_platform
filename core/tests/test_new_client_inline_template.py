"""Tests for the inline-template-by-default behavior of the new-client form.

The new-client form ("/dashboard/sites/new/") is supposed to be the agency's
one-screen flow: paste a URL → fetch → annotate → submit. To remove the
"create a template first" detour, the inline-new-template block is shown by
default and a Fetch-from-URL input lives alongside the Annotate-with-AI
button (same endpoints as the standalone template form, no backend changes).
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Template, Tenant


User = get_user_model()


@override_settings(TENANT_BASE_DOMAIN="localhost")
class NewClientFormInlineTemplateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.saved_template = Template.objects.create(
            name="Saved one",
            description="",
            html_source="<section data-section='hero' data-label='Hero'></section>",
        )

    def _get(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c.get(reverse("dashboard:tenant_create"))

    def test_inline_new_template_block_is_visible_by_default(self):
        """The inline block must NOT carry the `hidden` attribute on first
        render — that's what makes "paste URL → annotate" the default flow."""
        html = self._get().content.decode()
        # Find the new-template-block div opening tag.
        idx = html.find('id="new-template-block"')
        self.assertNotEqual(idx, -1, "new-template-block container missing")
        # Slice the opening tag (up to the next `>`) and assert no `hidden` attr.
        opening = html[idx : html.find(">", idx)]
        self.assertNotIn(
            " hidden", opening,
            "new-template-block must NOT be hidden by default — "
            "inline template-create is the primary flow.",
        )

    def test_template_dropdown_defaults_to_new(self):
        """The template <select> initial value is `__new__` so non-JS / SSR
        view of the form already reflects the inline-by-default choice."""
        html = self._get().content.decode()
        self.assertIn(
            'value="__new__" selected',
            html,
            "The '+ Create new template inline' option must be pre-selected.",
        )

    def test_fetch_url_input_is_present_in_inline_block(self):
        """Fetch-from-URL was added to the standalone template form earlier;
        it now also lives in the inline block of the new-client flow so the
        operator can paste a *.pages.dev URL and have the HTML fetched
        without leaving this screen."""
        html = self._get().content.decode()
        self.assertIn('id="fetch-url-input"', html)
        self.assertIn('id="fetch-url-btn"', html)
        self.assertIn(
            reverse("dashboard:template_fetch_url"), html,
            "Fetch button must point at the existing template_fetch_url endpoint.",
        )

    def test_saved_template_disclosure_link_is_present(self):
        """A small 'Use a saved template instead' affordance reveals the
        saved-template dropdown for the (less common) re-use case."""
        html = self._get().content.decode().lower()
        self.assertIn("use a saved template", html)

    def test_saved_template_dropdown_still_in_dom(self):
        """The dropdown is collapsed behind the disclosure, but it's still in
        the DOM so a JS-disabled user (or progressive enhancement) can pick
        a saved template if they need to."""
        html = self._get().content.decode()
        self.assertIn('id="id_template"', html)
        self.assertIn(f'value="{self.saved_template.pk}"', html)

    def test_template_name_input_is_marked_optional(self):
        """Inline template-create is plug-and-forget — the operator doesn't
        have to name a template they won't reuse. The visible affordance
        next to 'Template name' must communicate that."""
        html = self._get().content.decode()
        # Locate the Template-name label and look in the surrounding ~400
        # chars (covers the label + input + helper) for an optionality cue.
        idx = html.find('for="id_new_template_name"')
        self.assertNotEqual(idx, -1, "Template name label not found")
        window = html[idx : idx + 400].lower()
        self.assertTrue(
            "optional" in window or "leave blank" in window or "we'll name it" in window,
            "Template name must be visibly marked optional in the inline flow. "
            f"Looked in: {window!r}",
        )


@override_settings(TENANT_BASE_DOMAIN="localhost")
class InlineTemplateCreatePlugAndForgetTests(TestCase):
    """The inline path on tenant_create should:
       1. Accept an empty 'new_template_name' and auto-derive it from the
          site name (plug-and-forget — no manual template naming required).
       2. Survive a name collision: two clients can have inline templates
          named the same; the slug must auto-unique itself."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)

    def _client(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c

    def _post(self, *, name, subdomain, client_username, template_name=""):
        return self._client().post(
            reverse("dashboard:tenant_create"),
            {
                "name": name,
                "subdomain": subdomain,
                "template": "__new__",
                "custom_domain": "",
                "client_username": client_username,
                "client_email": "",
                "new_template_name": template_name,
                "new_template_description": "",
                "new_template_html": (
                    "<section data-section='hero' data-label='Hero'>"
                    "<h1 data-edit='hero.title' data-type='text'>Hi</h1>"
                    "</section>"
                ),
            },
        )

    def test_empty_template_name_falls_back_to_site_name(self):
        response = self._post(
            name="Bella's Restaurant",
            subdomain="bellas",
            client_username="bella_owner",
        )
        # Expect a redirect (success), not a 400 with errors.
        self.assertIn(response.status_code, (301, 302), msg=response.content[:200])
        tenant = Tenant.objects.get(subdomain="bellas")
        self.assertIsNotNone(tenant.template_id)
        self.assertEqual(tenant.template.name, "Bella's Restaurant")

    def test_two_inline_templates_with_same_name_dont_collide_on_slug(self):
        """Without a uniqueness loop in Template.save(), the second insert
        would IntegrityError on the slug field."""
        r1 = self._post(
            name="Acme",
            subdomain="acme",
            client_username="acme_owner",
        )
        self.assertIn(r1.status_code, (301, 302))
        r2 = self._post(
            name="Acme",
            subdomain="acme2",
            client_username="acme2_owner",
        )
        self.assertIn(r2.status_code, (301, 302), msg=r2.content[:300])
        slugs = list(Template.objects.filter(name="Acme").values_list("slug", flat=True))
        self.assertEqual(len(slugs), 2)
        self.assertEqual(len(set(slugs)), 2, f"slugs collided: {slugs}")
