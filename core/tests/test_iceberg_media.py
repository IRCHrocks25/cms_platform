from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from core.models import Template, Tenant
from core.services import iceberg_media

IB = dict(
    ICEBERG_API_URL="https://api.test",
    ICEBERG_TOKEN="kic_test",
    ICEBERG_CDN="https://cdn.test",
    ICEBERG_TENANT="t1",
)


def _tenant():
    tpl = Template.objects.create(
        name="T", html_source="<section data-section='x'></section>"
    )
    owner = get_user_model().objects.create_user("owner", password="x")
    return Tenant.objects.create(
        name="Acme", subdomain="acme", template=tpl, owner=owner
    )


def _mock_client(put_url="https://r2.test/put?sig"):
    """Return a MagicMock standing in for httpx.Client() as a context manager."""
    client = mock.MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    post_resp = mock.MagicMock()
    post_resp.json.return_value = {"upload_url": put_url}
    post_resp.raise_for_status.return_value = None
    client.post.return_value = post_resp
    put_resp = mock.MagicMock()
    put_resp.raise_for_status.return_value = None
    client.put.return_value = put_resp
    return client


class IsConfiguredTests(TestCase):
    @override_settings(**IB)
    def test_configured_when_all_present(self):
        self.assertTrue(iceberg_media.is_configured())

    @override_settings(ICEBERG_API_URL="", ICEBERG_TOKEN="kic_x", ICEBERG_TENANT="t1")
    def test_not_configured_when_api_missing(self):
        self.assertFalse(iceberg_media.is_configured())


@override_settings(**IB)
class UploadImageTests(TestCase):
    def test_upload_image_runs_init_put_complete_and_returns_cdn_url(self):
        tenant = _tenant()
        upload = SimpleUploadedFile("Hero Shot.PNG", b"pngbytes", content_type="image/png")
        client = _mock_client()
        with mock.patch("core.services.iceberg_media.httpx.Client", return_value=client):
            result = iceberg_media.upload_image(upload, tenant)

        # init-upload, then complete = 2 POSTs; 1 PUT
        self.assertEqual(client.post.call_count, 2)
        self.assertEqual(client.put.call_count, 1)
        init_call = client.post.call_args_list[0]
        self.assertTrue(init_call.args[0].endswith("/assets/init-upload"))
        key = init_call.kwargs["json"]["key"]
        self.assertTrue(key.startswith("cms/tenants/acme/image/"))
        self.assertTrue(key.endswith(".png"))
        self.assertEqual(result["secure_url"], f"https://cdn.test/t1/{key}")
        self.assertEqual(result["public_id"], key)
        self.assertEqual(result["bytes"], upload.size)


@override_settings(**IB)
class UploadVideoTests(TestCase):
    def test_rejects_oversize_with_positive_cap(self):
        tenant = _tenant()
        upload = SimpleUploadedFile("clip.mp4", b"abcdefghij", content_type="video/mp4")
        with override_settings(MEDIA_MAX_VIDEO_BYTES=5):
            info, error = iceberg_media.upload_video(upload, tenant)
        self.assertIsNone(info)
        self.assertIn("too large", error)

    def test_rejects_non_video(self):
        tenant = _tenant()
        upload = SimpleUploadedFile("x.txt", b"hi", content_type="text/plain")
        info, error = iceberg_media.upload_video(upload, tenant)
        self.assertIsNone(info)
        self.assertIn("not a video", error)

    def test_uploads_video_and_returns_cdn_url(self):
        tenant = _tenant()
        upload = SimpleUploadedFile("clip.mp4", b"videodata", content_type="video/mp4")
        client = _mock_client()
        with mock.patch("core.services.iceberg_media.httpx.Client", return_value=client):
            info, error = iceberg_media.upload_video(upload, tenant)
        self.assertIsNone(error)
        self.assertTrue(info["public_id"].startswith("cms/tenants/acme/video/"))
        self.assertEqual(info["secure_url"], f"https://cdn.test/t1/{info['public_id']}")
