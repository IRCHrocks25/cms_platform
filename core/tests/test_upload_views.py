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


@override_settings(TENANT_BASE_DOMAIN="localhost", **IB)
class MediaGalleryViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.tpl = Template.objects.create(
            name="T",
            html_source=(
                "<section data-section='hero' data-label='Hero'>"
                "<img data-edit='hero.photo' data-type='image' data-label='Photo' "
                "src='https://cdn.example/landing-hero.jpg'>"
                "<img src='https://cdn.example/decor.png' alt=''>"
                "</section>"
            ),
        )
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.tpl, owner=_owner()
        )
        self.user = User.objects.create_user("g", password="p")
        TenantMembership.objects.create(
            tenant=self.tenant, user=self.user, role="owner"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_lists_images_newest_first(self):
        MediaAsset.objects.create(
            tenant=self.tenant,
            original_name="old.png",
            resource_type=MediaAsset.RESOURCE_IMAGE,
            secure_url="https://cdn.test/old.png",
            bytes=10,
        )
        MediaAsset.objects.create(
            tenant=self.tenant,
            original_name="new.png",
            resource_type=MediaAsset.RESOURCE_IMAGE,
            secure_url="https://cdn.test/new.png",
            bytes=20,
        )
        MediaAsset.objects.create(
            tenant=self.tenant,
            original_name="clip.mp4",
            resource_type=MediaAsset.RESOURCE_VIDEO,
            secure_url="https://cdn.test/clip.mp4",
            bytes=30,
        )
        resp = self.client.get(
            "/dashboard/editor/media/", HTTP_HOST="acme.localhost"
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        urls = [a["url"] for a in body["assets"]]
        # Uploads listed before harvested page images; both uploads present.
        self.assertIn("https://cdn.test/new.png", urls[:2])
        self.assertIn("https://cdn.test/old.png", urls[:2])
        self.assertIn("https://cdn.example/landing-hero.jpg", urls)
        self.assertIn("https://cdn.example/decor.png", urls)
        self.assertNotIn("https://cdn.test/clip.mp4", urls)
        self.assertLess(
            urls.index("https://cdn.test/new.png"),
            urls.index("https://cdn.example/landing-hero.jpg"),
        )

    def test_landing_page_images_without_uploads(self):
        resp = self.client.get(
            "/dashboard/editor/media/", HTTP_HOST="acme.localhost"
        )
        self.assertEqual(resp.status_code, 200)
        urls = {a["url"] for a in resp.json()["assets"]}
        self.assertEqual(
            urls,
            {
                "https://cdn.example/landing-hero.jpg",
                "https://cdn.example/decor.png",
            },
        )

    def test_content_override_image_appears(self):
        self.tenant.content = {
            "hero": {"photo": "https://cdn.example/client-upload.jpg"}
        }
        self.tenant.save(update_fields=["content"])
        resp = self.client.get(
            "/dashboard/editor/media/", HTTP_HOST="acme.localhost"
        )
        urls = {a["url"] for a in resp.json()["assets"]}
        self.assertIn("https://cdn.example/client-upload.jpg", urls)

    def test_empty_gallery_when_no_images_on_page(self):
        bare = Template.objects.create(
            name="Bare", html_source="<section data-section='x'></section>"
        )
        self.tenant.template = bare
        self.tenant.save(update_fields=["template"])
        resp = self.client.get(
            "/dashboard/editor/media/", HTTP_HOST="acme.localhost"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["assets"], [])

    def test_agency_gallery_endpoint(self):
        staff = get_user_model().objects.create_user(
            "staff", password="p", is_staff=True
        )
        self.client.force_login(staff)
        MediaAsset.objects.create(
            tenant=self.tenant,
            original_name="hero.jpg",
            resource_type=MediaAsset.RESOURCE_IMAGE,
            secure_url="https://cdn.test/hero.jpg",
        )
        resp = self.client.get(
            f"/dashboard/sites/{self.tenant.pk}/media/",
            HTTP_HOST="localhost",
        )
        self.assertEqual(resp.status_code, 200)
        assets = resp.json()["assets"]
        by_url = {a["url"]: a for a in assets}
        self.assertTrue(by_url["https://cdn.test/hero.jpg"]["editable"])
        self.assertFalse(by_url["https://cdn.example/landing-hero.jpg"]["editable"])

    def test_rename_upload(self):
        asset = MediaAsset.objects.create(
            tenant=self.tenant,
            original_name="old-name.png",
            resource_type=MediaAsset.RESOURCE_IMAGE,
            secure_url="https://cdn.test/pic.png",
        )
        resp = self.client.post(
            f"/dashboard/editor/media/{asset.id}/",
            data='{"name": "New name.png"}',
            content_type="application/json",
            HTTP_HOST="acme.localhost",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "New name.png")
        asset.refresh_from_db()
        self.assertEqual(asset.original_name, "New name.png")

    def test_delete_upload_scrubs_content(self):
        url = "https://cdn.test/to-delete.png"
        asset = MediaAsset.objects.create(
            tenant=self.tenant,
            original_name="to-delete.png",
            resource_type=MediaAsset.RESOURCE_IMAGE,
            secure_url=url,
        )
        self.tenant.content = {"hero": {"photo": url, "other": "keep"}}
        self.tenant.save(update_fields=["content"])
        resp = self.client.delete(
            f"/dashboard/editor/media/{asset.id}/",
            HTTP_HOST="acme.localhost",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertFalse(MediaAsset.objects.filter(pk=asset.id).exists())
        self.tenant.refresh_from_db()
        self.assertNotIn("photo", self.tenant.content["hero"])
        self.assertEqual(self.tenant.content["hero"]["other"], "keep")

    def test_rename_unknown_asset_404(self):
        resp = self.client.post(
            "/dashboard/editor/media/99999/",
            data='{"name": "x"}',
            content_type="application/json",
            HTTP_HOST="acme.localhost",
        )
        self.assertEqual(resp.status_code, 404)
