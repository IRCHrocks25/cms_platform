import json
import uuid

import httpx
from django.test import SimpleTestCase

from core.central_identity import RegistryDenied, TenantRegistryClient


class TenantRegistryClientTests(SimpleTestCase):
    def setUp(self):
        self.global_tenant_id = uuid.UUID("0199a0f2-2eeb-7000-8000-000000000001")
        self.requests = []

    def _client(self, response):
        def handler(request):
            self.requests.append(request)
            return response(request) if callable(response) else response

        return TenantRegistryClient(
            base_url="https://registry.example.test",
            token="registry-secret",
            timeout_seconds=2.0,
            transport=httpx.MockTransport(handler),
        )

    def test_active_binding_uses_exact_contract(self):
        reader = self._client(
            httpx.Response(
                200,
                json={
                    "globalTenantId": str(self.global_tenant_id),
                    "product": "cms",
                    "status": "active",
                    "localTenantId": "42",
                    "revision": "7",
                    "updatedAt": "2026-10-02T00:00:00Z",
                },
            )
        )

        decision = reader.require_active(self.global_tenant_id, "42")

        self.assertEqual(decision.revision, "7")
        request = self.requests[0]
        self.assertEqual(
            request.url.path,
            "/v1/businesses/0199a0f2-2eeb-7000-8000-000000000001/product-entitlements/cms",
        )
        self.assertEqual(request.headers["authorization"], "Bearer registry-secret")
        self.assertEqual(request.headers["accept"], "application/json")

    def test_denies_missing_suspended_mismatched_and_malformed_bindings(self):
        cases = {
            "missing": httpx.Response(404),
            "suspended": httpx.Response(
                200,
                json={
                    "globalTenantId": str(self.global_tenant_id),
                    "product": "cms",
                    "status": "suspended",
                    "localTenantId": "42",
                    "revision": "8",
                    "updatedAt": "2026-10-02T00:00:00Z",
                },
            ),
            "mismatch": httpx.Response(
                200,
                json={
                    "globalTenantId": str(self.global_tenant_id),
                    "product": "cms",
                    "status": "active",
                    "localTenantId": "999",
                    "revision": "9",
                    "updatedAt": "2026-10-02T00:00:00Z",
                },
            ),
            "malformed": httpx.Response(
                200,
                content=json.dumps({"status": "active"}),
                headers={"content-type": "application/json"},
            ),
        }

        for label, response in cases.items():
            with self.subTest(label=label), self.assertRaises(RegistryDenied):
                self._client(response).require_active(self.global_tenant_id, "42")

    def test_timeout_and_server_error_fail_closed_without_secret_in_error(self):
        def timeout(_request):
            raise httpx.ReadTimeout("upstream timeout")

        for response in (timeout, httpx.Response(503, text="registry unavailable")):
            with self.subTest(response=response), self.assertRaises(RegistryDenied) as caught:
                self._client(response).require_active(self.global_tenant_id, "42")
            self.assertNotIn("registry-secret", str(caught.exception))
