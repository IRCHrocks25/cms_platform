"""S11: durable, signed form delivery is replay safe."""
import uuid
import json
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import FormDelivery, FormSubmission, Template, Tenant
from core.services.business_forms import bind_business, queue_submission
from core.services.form_delivery import deliver_pending, verify_delivery_signature


@override_settings(SITES_FORM_ENCRYPTION_KEY=Fernet.generate_key().decode())
class FormDeliveryTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("delivery-owner", password="x")
        template = Template.objects.create(name="T", html_source="<html></html>")
        self.tenant = Tenant.objects.create(name="T", subdomain="delivery", owner=owner, template=template)
        self.binding = bind_business(
            tenant=self.tenant, business_id=uuid.uuid4(),
            receiver_url="https://receiver.example.test/forms", signing_secret="secret",
        )

    def _queue(self, event_id=None):
        return queue_submission(
            tenant=self.tenant, page=None, form_id="contact", email="person@example.test",
            name="Person", phone="", message="", consent=True,
            source={"page": "/", "utm_source": "ad"}, event_id=event_id,
        )

    @patch("core.services.form_delivery.httpx.post")
    def test_duplicate_event_delivers_once(self, post):
        post.return_value = Mock(status_code=200)
        event = uuid.uuid4()
        first = self._queue(event)
        second = self._queue(event)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(FormSubmission.objects.count(), 1)
        deliver_pending()
        deliver_pending()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(FormDelivery.objects.get().status, FormDelivery.DELIVERED)

    @patch("core.services.form_delivery.httpx.post")
    def test_outage_retries_same_event_and_signature_is_tenant_bound(self, post):
        submission = self._queue()
        post.side_effect = [OSError("offline"), Mock(status_code=200)]
        self.assertEqual(deliver_pending(), 0)
        self.assertEqual(FormDelivery.objects.get().status, FormDelivery.PENDING)
        self.assertEqual(deliver_pending(force=True), 1)
        self.assertEqual(post.call_count, 2)
        body = post.call_args.kwargs["content"]
        headers = post.call_args.kwargs["headers"]
        payload = json.loads(body)
        self.assertEqual(payload["event_id"], str(submission.event_id))
        self.assertEqual(payload["business_id"], str(self.binding.business_id))
        self.assertEqual(payload["source"]["utm_source"], "ad")
        self.assertEqual(payload["consent"], True)
        self.assertTrue(verify_delivery_signature(body, headers, "secret", str(self.tenant.pk)))
        self.assertFalse(verify_delivery_signature(body + b" ", headers, "secret", str(self.tenant.pk)))
        self.assertFalse(verify_delivery_signature(body, headers, "wrong", str(self.tenant.pk)))
        self.assertFalse(verify_delivery_signature(body, headers, "secret", "other-tenant"))

    def test_binding_cannot_be_changed_by_second_provisioning(self):
        self.assertNotIn("secret", self.binding.signing_secret_ciphertext)
        self.assertEqual(
            bind_business(
                tenant=self.tenant, business_id=self.binding.business_id,
                receiver_url=self.binding.receiver_url, signing_secret="secret",
            ).pk,
            self.binding.pk,
        )
        with self.assertRaises(ValueError):
            bind_business(
                tenant=self.tenant, business_id=uuid.uuid4(),
                receiver_url="https://other.example.test/forms", signing_secret="other",
            )

    def test_outbox_insert_failure_rolls_back_submission(self):
        with patch("core.services.business_forms.FormDelivery.objects.get_or_create", side_effect=ValueError("outbox failed")):
            with self.assertRaisesRegex(ValueError, "outbox failed"):
                self._queue()
        self.assertFalse(FormSubmission.objects.exists())
