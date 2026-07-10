from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import GhlAgencyInstall, GhlInstall


class GhlModelTests(TestCase):
    def test_agency_company_id_is_unique(self):
        GhlAgencyInstall.objects.create(company_id="co_1")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GhlAgencyInstall.objects.create(company_id="co_1")

    def test_install_links_agency_and_defaults_connected(self):
        agency = GhlAgencyInstall.objects.create(company_id="co_2")
        install = GhlInstall.objects.create(
            location_id="loc_1", agency=agency, access_token="enc"
        )
        self.assertEqual(install.agency, agency)
        self.assertEqual(install.status, GhlInstall.STATUS_CONNECTED)
        self.assertEqual(list(agency.location_installs.all()), [install])
