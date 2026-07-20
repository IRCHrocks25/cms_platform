from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from core.models import MediaAsset, Template, TenantMembership, Tenant

IB = dict(
    ICEBERG_API_URL="https://api.test",
    ICEBERG_TOKEN="kic_test",
    ICEBERG_CDN="https://cdn.test",
    ICEBERG_TENANT="t1",
)

# A tiny valid 1x1 PNG so validate_image (Pillow) passes.
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63382127070002b6010534a675aa0000000049454e44ae426082"
)


def _owner():
    return get_user_model().objects.create_user("owner", password="x")


@override_settings(TENANT_BASE_DOMAIN="localhost", **IB)
class ImageUploadViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.tpl = Template.objects.create(
            name="T", html_source="<section data-section='x'></section>"
        )
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.tpl, owner=_owner()
        )
        self.user = User.objects.create_user("u", password="p")
        TenantMembership.objects.create(
            tenant=self.tenant, user=self.user, role="owner"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _post(self, data):
        upload = SimpleUploadedFile("pic.png", data, content_type="image/png")
        return self.client.post(
            "/dashboard/editor/upload/", {"file": upload}, HTTP_HOST="acme.localhost"
        )

    def test_successful_image_upload_stores_cdn_url(self):
        with mock.patch(
            "core.services.iceberg_media.upload_image",
            return_value={
                "public_id": "cms/tenants/acme/image/ab-pic.png",
                "secure_url": "https://cdn.test/t1/cms/tenants/acme/image/ab-pic.png",
                "delivery_url": "https://cdn.test/t1/cms/tenants/acme/image/ab-pic.png",
                "bytes": len(PNG_1x1),
            },
        ):
            resp = self._post(PNG_1x1)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(
            body["url"], "https://cdn.test/t1/cms/tenants/acme/image/ab-pic.png"
        )
        asset = MediaAsset.objects.get(id=body["id"])
        self.assertEqual(asset.secure_url, body["url"])
        self.assertEqual(asset.resource_type, MediaAsset.RESOURCE_IMAGE)

    def test_rejects_non_image(self):
        resp = self._post(b"not an image")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    @override_settings(ICEBERG_API_URL="", ICEBERG_TOKEN="", ICEBERG_TENANT="")
    def test_not_configured_returns_clean_error(self):
        resp = self._post(PNG_1x1)
        self.assertEqual(resp.status_code, 500)
        self.assertIn("configured", resp.json()["error"])


@override_settings(TENANT_BASE_DOMAIN="localhost", **IB)
class VideoUploadViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.tpl = Template.objects.create(
            name="T", html_source="<section data-section='x'></section>"
        )
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.tpl, owner=_owner()
        )
        self.user = User.objects.create_user("v", password="p")
        TenantMembership.objects.create(
            tenant=self.tenant, user=self.user, role="owner"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _post(self, content_type="video/mp4"):
        upload = SimpleUploadedFile("clip.mp4", b"videobytes", content_type=content_type)
        return self.client.post(
            "/dashboard/editor/video-upload/",
            {"file": upload},
            HTTP_HOST="acme.localhost",
        )

    def test_successful_video_upload_stores_cdn_url(self):
        with mock.patch(
            "core.services.iceberg_media.upload_video",
            return_value=(
                {
                    "public_id": "cms/tenants/acme/video/ab-clip.mp4",
                    "secure_url": "https://cdn.test/t1/cms/tenants/acme/video/ab-clip.mp4",
                    "bytes": 10,
                },
                None,
            ),
        ):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        asset = MediaAsset.objects.get(id=body["id"])
        self.assertEqual(asset.resource_type, MediaAsset.RESOURCE_VIDEO)
        self.assertEqual(asset.secure_url, body["url"])

    def test_rejects_non_video(self):
        resp = self._post(content_type="text/plain")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])
