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
    that context, so the user can never stay logged in. IFRAME_EMBED=1 flips
    the session + CSRF cookies to SameSite=None; Secure. Default off so
    plain-HTTP dev doesn't silently drop cookies."""

    def tearDown(self):
        import cms_platform.settings as settings_module
        importlib.reload(settings_module)

    def test_iframe_embed_uses_samesite_none_and_secure(self):
        s = _reload_settings_with_env(IFRAME_EMBED="1")
        self.assertTrue(s.IFRAME_EMBED)
        self.assertEqual(s.SESSION_COOKIE_SAMESITE, "None")
        self.assertTrue(s.SESSION_COOKIE_SECURE)
        self.assertEqual(s.CSRF_COOKIE_SAMESITE, "None")
        self.assertTrue(s.CSRF_COOKIE_SECURE)

    def test_default_keeps_safe_lax_cookies(self):
        s = _reload_settings_with_env(IFRAME_EMBED="0")
        self.assertFalse(s.IFRAME_EMBED)
        self.assertEqual(s.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertFalse(s.SESSION_COOKIE_SECURE)
        self.assertEqual(s.CSRF_COOKIE_SAMESITE, "Lax")
        self.assertFalse(s.CSRF_COOKIE_SECURE)
