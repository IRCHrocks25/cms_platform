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

from core.models import Template


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
