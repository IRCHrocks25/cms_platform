from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Template, Tenant
from core.management.commands.migrate_cloudinary_to_iceberg import cloudinary_key

IB = dict(
    ICEBERG_API_URL="https://api.test",
    ICEBERG_TOKEN="kic_test",
    ICEBERG_CDN="https://cdn.test",
    ICEBERG_TENANT="t1",
)

CLOUD_URL = "https://res.cloudinary.com/dcuswyfur/image/upload/v1777474708/paula_hidalgo_loog_dwnugb.png"
NEW_URL = "https://cdn.test/t1/cloudinary/paula_hidalgo_loog_dwnugb.png"


def _tenant_with_url():
    tpl = Template.objects.create(
        name="T", html_source="<section data-section='x'></section>"
    )
    owner = get_user_model().objects.create_user("owner", password="x")
    return Tenant.objects.create(
        name="Acme",
        subdomain="acme",
        template=tpl,
        owner=owner,
        content={"hero": {"image": CLOUD_URL}},
    )


class CloudinaryKeyTests(TestCase):
    def test_derives_key_without_version_segment(self):
        self.assertEqual(
            cloudinary_key(CLOUD_URL), "cloudinary/paula_hidalgo_loog_dwnugb.png"
        )

    def test_preserves_nested_public_id(self):
        url = "https://res.cloudinary.com/dc/image/upload/v1/cms/tenants/acme/a_b.jpg"
        self.assertEqual(cloudinary_key(url), "cloudinary/cms/tenants/acme/a_b.jpg")


@override_settings(**IB)
class MigrateCommandTests(TestCase):
    def test_dry_run_makes_no_writes(self):
        t = _tenant_with_url()
        with mock.patch("httpx.get") as get, mock.patch(
            "core.services.iceberg_media.upload_bytes"
        ) as up:
            call_command("migrate_cloudinary_to_iceberg")  # no --apply
        get.assert_not_called()
        up.assert_not_called()
        t.refresh_from_db()
        self.assertEqual(t.content["hero"]["image"], CLOUD_URL)

    def test_apply_downloads_and_rewrites_when_not_on_cdn(self):
        t = _tenant_with_url()
        head = mock.MagicMock(status_code=404)
        resp = mock.MagicMock()
        resp.content = b"imgbytes"
        resp.raise_for_status.return_value = None
        resp.headers = {"Content-Type": "image/png"}
        with mock.patch("httpx.head", return_value=head), mock.patch(
            "httpx.get", return_value=resp
        ), mock.patch(
            "core.services.iceberg_media.upload_bytes", return_value=NEW_URL
        ) as up:
            call_command("migrate_cloudinary_to_iceberg", "--apply")
        up.assert_called_once()
        self.assertEqual(
            up.call_args.args[1], "cloudinary/paula_hidalgo_loog_dwnugb.png"
        )
        t.refresh_from_db()
        self.assertEqual(t.content["hero"]["image"], NEW_URL)

    def test_apply_rewrites_template_defaults(self):
        tpl = Template.objects.create(
            name="Tpl",
            html_source=(
                "<section data-section='x'>"
                f"<img data-edit='x.img' data-type='image' src='{CLOUD_URL}'>"
                "</section>"
            ),
        )
        head = mock.MagicMock(status_code=200)
        with mock.patch("httpx.head", return_value=head), mock.patch("httpx.get"), mock.patch(
            "core.services.iceberg_media.upload_bytes"
        ):
            call_command("migrate_cloudinary_to_iceberg", "--apply")
        tpl.refresh_from_db()
        self.assertNotIn("res.cloudinary.com", tpl.html_source)
        self.assertIn(NEW_URL, tpl.html_source)

    def test_apply_reuses_when_already_on_cdn(self):
        t = _tenant_with_url()
        head = mock.MagicMock(status_code=200)
        with mock.patch("httpx.head", return_value=head), mock.patch(
            "httpx.get"
        ) as get, mock.patch(
            "core.services.iceberg_media.upload_bytes"
        ) as up:
            call_command("migrate_cloudinary_to_iceberg", "--apply")
        # already hosted -> no download, no upload, but URL is rewritten
        get.assert_not_called()
        up.assert_not_called()
        t.refresh_from_db()
        self.assertEqual(t.content["hero"]["image"], NEW_URL)

    def test_apply_is_idempotent(self):
        t = _tenant_with_url()
        head = mock.MagicMock(status_code=200)
        with mock.patch("httpx.head", return_value=head), mock.patch(
            "core.services.iceberg_media.upload_bytes"
        ):
            call_command("migrate_cloudinary_to_iceberg", "--apply")
        # second run: nothing left to migrate
        with mock.patch("httpx.head") as h, mock.patch("httpx.get") as get, mock.patch(
            "core.services.iceberg_media.upload_bytes"
        ) as up:
            call_command("migrate_cloudinary_to_iceberg", "--apply")
        h.assert_not_called()
        get.assert_not_called()
        up.assert_not_called()
