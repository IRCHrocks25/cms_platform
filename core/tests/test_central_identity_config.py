from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.central_identity_config import load_central_identity_config


class CentralIdentityConfigTests(SimpleTestCase):
    def test_disabled_configuration_is_inert(self):
        config = load_central_identity_config(
            {
                "KATEK_OIDC_ENABLED": "false",
                "KATEK_OIDC_CLIENT_ID": "partial-value-is-ignored",
            }
        )

        self.assertFalse(config.enabled)

    def test_enabled_partial_configuration_names_every_missing_setting(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            load_central_identity_config({"KATEK_OIDC_ENABLED": "true"})

        message = str(caught.exception)
        for name in (
            "KATEK_OIDC_ISSUER",
            "KATEK_OIDC_CLIENT_ID",
            "KATEK_OIDC_CLIENT_SECRET",
            "KATEK_OIDC_AUTHORIZATION_ENDPOINT",
            "KATEK_OIDC_TOKEN_ENDPOINT",
            "KATEK_OIDC_USERINFO_ENDPOINT",
            "KATEK_OIDC_JWKS_ENDPOINT",
            "KATEK_TENANT_REGISTRY_URL",
            "KATEK_TENANT_REGISTRY_TOKEN",
        ):
            self.assertIn(name, message)

    def test_enabled_configuration_requires_https(self):
        values = self._complete_values()
        values["KATEK_OIDC_ISSUER"] = "http://login.example.test"

        with self.assertRaisesMessage(ImproperlyConfigured, "KATEK_OIDC_ISSUER"):
            load_central_identity_config(values)

    def test_enabled_configuration_pins_rs256_nonce_and_pkce(self):
        config = load_central_identity_config(self._complete_values())

        self.assertTrue(config.enabled)
        self.assertEqual(config.issuer, "https://login.example.test")
        self.assertEqual(config.signing_algorithm, "RS256")
        self.assertTrue(config.use_nonce)
        self.assertEqual(config.pkce_method, "S256")
        self.assertEqual(config.entitlement_cache_seconds, 300)

    @staticmethod
    def _complete_values():
        return {
            "KATEK_OIDC_ENABLED": "true",
            "KATEK_OIDC_ISSUER": "https://login.example.test",
            "KATEK_OIDC_CLIENT_ID": "cms-client",
            "KATEK_OIDC_CLIENT_SECRET": "secret",
            "KATEK_OIDC_AUTHORIZATION_ENDPOINT": "https://login.example.test/oauth/v2/authorize",
            "KATEK_OIDC_TOKEN_ENDPOINT": "https://login.example.test/oauth/v2/token",
            "KATEK_OIDC_USERINFO_ENDPOINT": "https://login.example.test/oidc/v1/userinfo",
            "KATEK_OIDC_JWKS_ENDPOINT": "https://login.example.test/oauth/v2/keys",
            "KATEK_TENANT_REGISTRY_URL": "https://registry.example.test",
            "KATEK_TENANT_REGISTRY_TOKEN": "registry-secret",
        }
