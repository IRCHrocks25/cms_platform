from django.contrib import admin
from django.test import TestCase

from core.models import GhlAgencyInstall, GhlInstall


class GhlAdminRegistrationTests(TestCase):
    def test_models_registered(self):
        self.assertIn(GhlAgencyInstall, admin.site._registry)
        self.assertIn(GhlInstall, admin.site._registry)
