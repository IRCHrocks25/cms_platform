import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import TestCase, override_settings
from django.urls import reverse

from core import ghl_webhook
from core.models import GhlAgencyInstall, GhlInstall

# A dedicated test keypair (NOT GHL's real webhook key).
_PRIV = Ed25519PrivateKey.generate()
_PUB_PEM = _PRIV.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()


def _sign(body: bytes) -> str:
    return base64.b64encode(_PRIV.sign(body)).decode()


@override_settings(GHL_WEBHOOK_PUBLIC_KEY=_PUB_PEM, ALLOWED_HOSTS=["testserver"])
class WebhookSignatureTests(TestCase):
    def test_verify_signature_valid(self):
        body = b'{"type":"INSTALL"}'
        self.assertTrue(ghl_webhook.verify_signature(body=body, signature_b64=_sign(body)))

    def test_verify_signature_rejects_tampered_body(self):
        body = b'{"type":"INSTALL"}'
        sig = _sign(body)
        self.assertFalse(
            ghl_webhook.verify_signature(body=b'{"type":"HACKED"}', signature_b64=sig)
        )

    def test_verify_signature_rejects_garbage(self):
        self.assertFalse(ghl_webhook.verify_signature(body=b"x", signature_b64="not-base64!!"))
        self.assertFalse(ghl_webhook.verify_signature(body=b"x", signature_b64="AAAA"))

    def test_webhook_accepts_valid_signature(self):
        body = b'{"type":"INSTALL"}'
        resp = self.client.post(
            reverse("ghl_webhook"), data=body, content_type="application/json",
            HTTP_X_GHL_SIGNATURE=_sign(body),
        )
        self.assertEqual(resp.status_code, 200)

    def test_webhook_rejects_missing_or_bad_signature(self):
        body = b'{"type":"INSTALL"}'
        # no header
        r1 = self.client.post(reverse("ghl_webhook"), data=body, content_type="application/json")
        self.assertEqual(r1.status_code, 401)
        # wrong signature
        r2 = self.client.post(
            reverse("ghl_webhook"), data=body, content_type="application/json",
            HTTP_X_GHL_SIGNATURE="AAAA",
        )
        self.assertEqual(r2.status_code, 401)

    def test_webhook_uninstall_marks_install_disconnected(self):
        agency = GhlAgencyInstall.objects.create(company_id="co_1")
        inst = GhlInstall.objects.create(
            location_id="loc_a", agency=agency, access_token="x",
            status=GhlInstall.STATUS_CONNECTED,
        )
        body = json.dumps({"type": "UNINSTALL", "locationId": "loc_a"}).encode()
        resp = self.client.post(
            reverse("ghl_webhook"), data=body, content_type="application/json",
            HTTP_X_GHL_SIGNATURE=_sign(body),
        )
        self.assertEqual(resp.status_code, 200)
        inst.refresh_from_db()
        self.assertEqual(inst.status, GhlInstall.STATUS_DISCONNECTED)


@override_settings(GHL_WEBHOOK_PUBLIC_KEY="", ALLOWED_HOSTS=["testserver"])
class WebhookUnconfiguredTests(TestCase):
    def test_webhook_accepts_unverified_when_key_absent(self):
        # Transition behavior: unconfigured key -> accept (logged), do not drop.
        resp = self.client.post(
            reverse("ghl_webhook"), data=b'{"type":"INSTALL"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)


@override_settings(ALLOWED_HOSTS=["testserver"])
class WebhookKeyTests(TestCase):
    def test_escaped_newline_key_still_verifies(self):
        # The env holds the PEM as a single line with literal \n; must normalize.
        escaped = _PUB_PEM.replace("\n", "\\n")
        body = b'{"type":"INSTALL"}'
        with override_settings(GHL_WEBHOOK_PUBLIC_KEY=escaped):
            self.assertTrue(
                ghl_webhook.verify_signature(body=body, signature_b64=_sign(body))
            )

    def test_bare_base64_key_verifies(self):
        # Env may hold just the base64 SPKI (no PEM header); code wraps it.
        bare = (
            _PUB_PEM.replace("-----BEGIN PUBLIC KEY-----", "")
            .replace("-----END PUBLIC KEY-----", "")
            .replace("\n", "")
            .strip()
        )
        body = b'{"type":"INSTALL"}'
        with override_settings(GHL_WEBHOOK_PUBLIC_KEY=bare):
            self.assertTrue(
                ghl_webhook.verify_signature(body=body, signature_b64=_sign(body))
            )

    def test_real_ghl_key_is_valid_ed25519(self):
        # GHL's published global webhook Ed25519 key (from the marketplace docs).
        # Locks the value in so a future typo fails CI.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        real = (
            "-----BEGIN PUBLIC KEY-----\n"
            "MCowBQYDK2VwAyEAi2HR1srL4o18O8BRa7gVJY7G7bupbN3H9AwJrHCDiOg=\n"
            "-----END PUBLIC KEY-----"
        )
        self.assertIsInstance(load_pem_public_key(real.encode()), Ed25519PublicKey)
