from io import StringIO

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import Resolver404, resolve
from oauth2_provider.models import AccessToken, Application


class ApiScaffoldTests(TestCase):
    def test_api_and_oauth_apps_are_installed(self):
        self.assertIn("api", settings.INSTALLED_APPS)
        self.assertIn("oauth2_provider", settings.INSTALLED_APPS)

    def test_health_endpoint_returns_json(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_oauth_server_metadata_is_routable(self):
        response = self.client.get("/.well-known/oauth-authorization-server")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authorization_endpoint"].endswith("/authorize/"))
        self.assertTrue(response.json()["token_endpoint"].endswith("/token/"))

    def test_protected_resource_metadata_is_routable(self):
        response = self.client.get("/.well-known/oauth-protected-resource")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resource"], "http://testserver")
        self.assertEqual(response.json()["authorization_servers"], ["http://testserver"])

    def test_oauth_provider_enforces_pkce_and_disables_dynamic_registration(self):
        self.assertTrue(settings.OAUTH2_PROVIDER["PKCE_REQUIRED"])
        self.assertTrue(settings.OAUTH2_PROVIDER["COMPLIANT_BCP_RFC9700_PKCE_METHOD"])
        self.assertFalse(settings.OAUTH2_PROVIDER["DCR_ENABLED"])

    def test_only_static_oauth_client_registration_is_routed(self):
        for registration_path in ("/register/", "/applications/register/"):
            with self.subTest(path=registration_path):
                try:
                    match = resolve(registration_path)
                except Resolver404:
                    continue
                self.assertNotIn(match.url_name, {"dcr-register", "register"})

    def test_oauth_provider_binds_tokens_to_rfc8707_resource(self):
        token = AccessToken(resource=["https://sites.example/api"])

        self.assertTrue(token.allows_audience("https://sites.example/api/pages"))
        self.assertFalse(token.allows_audience("https://other.example/api"))


class RegisterClaudeOAuthClientTests(TestCase):
    @override_settings(
        CLAUDE_OAUTH_CLIENT_ID="",
        CLAUDE_OAUTH_CLIENT_SECRET="",
        CLAUDE_OAUTH_REDIRECT_URIS="",
    )
    def test_command_requires_environment_backed_client_configuration(self):
        with self.assertRaisesMessage(
            CommandError,
            "CLAUDE_OAUTH_CLIENT_ID, CLAUDE_OAUTH_CLIENT_SECRET, and "
            "CLAUDE_OAUTH_REDIRECT_URIS are required",
        ):
            call_command("register_claude_oauth_client")

    @override_settings(
        CLAUDE_OAUTH_CLIENT_ID="claude-client",
        CLAUDE_OAUTH_CLIENT_SECRET="test-only-secret",
        CLAUDE_OAUTH_REDIRECT_URIS=(
            "https://claude.ai/api/mcp/auth_callback "
            "https://claude.com/api/mcp/auth_callback"
        ),
    )
    def test_command_registers_one_confidential_authorization_code_client(self):
        first_output = StringIO()
        second_output = StringIO()

        call_command("register_claude_oauth_client", stdout=first_output)
        call_command("register_claude_oauth_client", stdout=second_output)

        application = Application.objects.get(name="Claude")
        self.assertEqual(Application.objects.filter(name="Claude").count(), 1)
        self.assertEqual(application.client_id, "claude-client")
        self.assertEqual(application.client_type, Application.CLIENT_CONFIDENTIAL)
        self.assertEqual(
            application.authorization_grant_type,
            Application.GRANT_AUTHORIZATION_CODE,
        )
        self.assertEqual(
            application.redirect_uris,
            "https://claude.ai/api/mcp/auth_callback "
            "https://claude.com/api/mcp/auth_callback",
        )
        self.assertTrue(check_password("test-only-secret", application.client_secret))
        self.assertNotIn("test-only-secret", first_output.getvalue())
        self.assertNotIn("test-only-secret", second_output.getvalue())
