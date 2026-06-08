import importlib
import os
from unittest import mock

from django.test import TestCase


def _reload_settings_with_env(**env):
    import cms_platform.settings as settings_module
    with mock.patch.dict(os.environ, env, clear=False):
        return importlib.reload(settings_module)


class IframeCookieSettingsTests(TestCase):
    """sites.katek.app is embedded as an iframe inside a white-labeled GHL
    dashboard (different origin). Browsers won't send SameSite=Lax cookies in
    that context, so the user can never stay logged in. Production must use
    SameSite=None; Secure on session + CSRF cookies. Dev (DEBUG=1, plain HTTP)
    must keep the safe defaults — browsers reject Secure cookies on http://."""

    def tearDown(self):
        import cms_platform.settings as settings_module
        importlib.reload(settings_module)

    def test_production_uses_samesite_none_and_secure(self):
        s = _reload_settings_with_env(DJANGO_DEBUG="0")
        self.assertFalse(s.DEBUG)
        self.assertEqual(s.SESSION_COOKIE_SAMESITE, "None")
        self.assertTrue(s.SESSION_COOKIE_SECURE)
        self.assertEqual(s.CSRF_COOKIE_SAMESITE, "None")
        self.assertTrue(s.CSRF_COOKIE_SECURE)

    def test_dev_keeps_safe_defaults(self):
        s = _reload_settings_with_env(DJANGO_DEBUG="1")
        self.assertTrue(s.DEBUG)
        self.assertEqual(s.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertFalse(s.SESSION_COOKIE_SECURE)
        self.assertEqual(s.CSRF_COOKIE_SAMESITE, "Lax")
        self.assertFalse(s.CSRF_COOKIE_SECURE)
