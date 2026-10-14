"""S11: a Sites form persists a tenant-bound lead before acknowledging it."""
import uuid
from unittest.mock import patch

from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from core.models import FormSubmission, FormDelivery, Template, Tenant, Page
from core.parser import build_schema
from core.services.business_forms import bind_business, form_token, inventory_form_capture
from core.services.ghl_embed_slots import external_form_slots


@override_settings(
    SITES_BUSINESS_FORMS_ENABLED=True, ALLOWED_HOSTS=["*"],
    SITES_FORM_ENCRYPTION_KEY=Fernet.generate_key().decode(),
)
class BusinessFormTests(TestCase):
    def setUp(self):
        cache.clear()
        owner = get_user_model().objects.create_user("form-owner", password="x")
        self.template = Template.objects.create(
            name="Forms", html_source='<html><body><div data-cms-form="contact"></div></body></html>'
        )
        self.tenant = Tenant.objects.create(
            name="Forms", subdomain="forms", template=self.template,
            owner=owner, is_published=True,
        )
        self.page = Page.objects.create(
            tenant=self.tenant, template=self.template, title="Contact",
            slug="contact", is_published=True,
        )
        self.binding = bind_business(
            tenant=self.tenant, business_id=uuid.uuid4(),
            receiver_url="https://receiver.example.test/forms", signing_secret="test-secret",
        )
        self.host = "forms.localhost"

    def _post(self, **extra):
        data = {
            "form_token": form_token(self.tenant, self.page, "contact"),
            "email": "person@example.test", "name": "Person",
            "utm_source": "newsletter", "consent": "yes",
        }
        data.update(extra)
        return self.client.post(
            "/api/site-forms/submit/", data,
            HTTP_HOST=self.host, HTTP_ORIGIN="http://forms.localhost",
        )

    @patch("core.services.form_delivery.httpx.post")
    def test_ordinary_page_queues_during_receiver_outage(self, post):
        post.side_effect = OSError("receiver unavailable")
        html = self.client.get("/contact?utm_source=newsletter", HTTP_HOST=self.host).content.decode()
        form = BeautifulSoup(html, "html.parser").find("form", attrs={"data-cms-form": "contact"})
        self.assertIsNotNone(form)
        self.assertEqual(form["action"], "/api/site-forms/submit/")
        self.assertEqual(form.find("input", attrs={"name": "utm_source"})["value"], "newsletter")
        response = self._post()
        self.assertEqual(response.status_code, 202)
        submission = FormSubmission.objects.get()
        self.assertEqual(submission.tenant, self.tenant)
        self.assertEqual(submission.page, self.page)
        self.assertEqual(submission.business_id, self.binding.business_id)
        self.assertEqual(submission.source["utm_source"], "newsletter")
        self.assertEqual(FormDelivery.objects.get().status, FormDelivery.PENDING)

    def test_agency_host_fallback_form_uses_signed_site_path(self):
        html = self.client.get("/site/forms/contact/", HTTP_HOST="localhost").content.decode()
        form = BeautifulSoup(html, "html.parser").find("form", attrs={"data-cms-form": "contact"})
        self.assertEqual(form["action"], "/site/forms/api/site-forms/submit/")
        response = self.client.post(
            form["action"],
            {"form_token": form.find("input", attrs={"name": "form_token"})["value"],
             "email": "person@example.test"},
            HTTP_HOST="localhost", HTTP_ORIGIN="http://localhost",
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(FormSubmission.objects.get().tenant, self.tenant)

    def test_client_org_cannot_route_or_override_binding(self):
        response = self._post(organization_id=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(FormSubmission.objects.exists())

    def test_tampered_token_and_cross_origin_are_rejected(self):
        self.assertEqual(self._post(form_token="bad").status_code, 400)
        response = self.client.post(
            "/api/site-forms/submit/",
            {"form_token": form_token(self.tenant, self.page, "contact"), "email": "x@example.test"},
            HTTP_HOST=self.host, HTTP_ORIGIN="https://attacker.example",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(FormSubmission.objects.exists())

    def test_honeypot_does_not_consume_valid_submission(self):
        self.assertEqual(self._post(website="spam").status_code, 400)
        self.assertEqual(self._post().status_code, 202)
        self.assertEqual(FormSubmission.objects.count(), 1)

    def test_rate_limit_rejects_excess_without_losing_accepted_events(self):
        for _ in range(10):
            self.assertEqual(self._post().status_code, 202)
        self.assertEqual(self._post().status_code, 429)
        self.assertEqual(FormSubmission.objects.count(), 10)
        self.assertEqual(FormDelivery.objects.count(), 10)

    def test_other_tenant_cannot_redeem_form_token(self):
        other = Tenant.objects.create(
            name="Other", subdomain="other", template=self.template,
            owner=self.tenant.owner, is_published=True,
        )
        bind_business(
            tenant=other, business_id=uuid.uuid4(),
            receiver_url="https://receiver.example.test/forms", signing_secret="other-secret",
        )
        response = self.client.post(
            "/api/site-forms/submit/",
            {"form_token": form_token(self.tenant, self.page, "contact"), "email": "x@example.test"},
            HTTP_HOST="other.localhost", HTTP_ORIGIN="http://other.localhost",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(FormSubmission.objects.exists())

    def test_disabled_feature_refuses_submission(self):
        with override_settings(SITES_BUSINESS_FORMS_ENABLED=False):
            self.assertEqual(self._post().status_code, 503)
        self.assertFalse(FormSubmission.objects.exists())

    def test_inventory_flags_external_form_embeds_for_publication_review(self):
        inventory = inventory_form_capture(
            '<div data-cms-form="contact"></div>'
            '<iframe src="https://forms.example.test/embed"></iframe>'
        )
        self.assertEqual(inventory["native"], ["contact"])
        self.assertEqual(inventory["unsupported"], ["https://forms.example.test/embed"])
        schema = build_schema(
            '<section data-section="contact"><div data-edit="contact.embed" '
            'data-type="ghl-embed" data-ghl-kind="form"></div></section>'
        )
        self.assertEqual(
            external_form_slots(schema, {"contact": {"embed": "form:external_id"}}),
            ["contact.embed"],
        )
