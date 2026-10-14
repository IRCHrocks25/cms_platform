"""api.0003 downgrade must not expose pending credentials to S03 auth."""

import hashlib
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from api.models import ServiceCredential, SuiteCredentialHandoff
from api.service_auth import resolve_service_token
from core.models import SuiteTenantBinding, Template, Tenant


class SuiteHandoffMigrationRollbackTests(TransactionTestCase):
    migrate_before = [("api", "0003_suitecredentialhandoff_and_more")]
    migrate_after = [("api", "0002_service_credential")]

    def setUp(self):
        self._migrate(self.migrate_before)
        owner = get_user_model().objects.create_user("rollback-owner", password="x")
        template = Template.objects.create(name="Rollback", html_source="<p>test</p>")
        self.tenant = Tenant.objects.create(
            name="Rollback", subdomain="rollback", owner=owner, template=template,
        )
        self.binding = SuiteTenantBinding.objects.create(
            business_id=uuid.uuid4(), operation_key=uuid.uuid4(),
            tenant=self.tenant, owner=owner,
            mode=SuiteTenantBinding.MODE_CREATE, request_hash="x" * 64,
        )

    def tearDown(self):
        self._migrate(self.migrate_before)
        super().tearDown()

    def _migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def _credential(self, *, is_pending, suffix):
        key_id = uuid.uuid4()
        token = f"sites_{key_id.hex}_{suffix * 43}"
        credential = ServiceCredential.objects.create(
            key_id=key_id, tenant=self.tenant,
            token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
            scopes=["read", "draft"], is_pending=is_pending,
            expires_at=timezone.now() + timedelta(days=1),
        )
        return credential, token

    def _handoff(self, pending, *, active=None):
        return SuiteCredentialHandoff.objects.create(
            operation_key=uuid.uuid4(), binding=self.binding,
            credential=pending, previous_credential=active,
            scopes=["read", "draft"], rotate=active is not None,
            ciphertext="synthetic-ciphertext", expires_at=timezone.now() + timedelta(minutes=15),
        )

    def test_pending_only_downgrade_revokes_token_before_old_resolver_can_accept_it(self):
        pending, token = self._credential(is_pending=True, suffix="p")
        self._handoff(pending)
        self.assertIsNone(resolve_service_token(token))

        apps = self._migrate(self.migrate_after)
        old_credential = apps.get_model("api", "ServiceCredential").objects.get(pk=pending.pk)
        self.assertIsNotNone(old_credential.revoked_at)

    def test_active_plus_pending_downgrade_aborts_without_partial_changes(self):
        active, _ = self._credential(is_pending=False, suffix="a")
        pending, token = self._credential(is_pending=True, suffix="p")
        handoff = self._handoff(pending, active=active)

        with self.assertRaisesRegex(RuntimeError, "active and pending"):
            self._migrate(self.migrate_after)

        active.refresh_from_db()
        pending.refresh_from_db()
        self.assertIsNone(active.revoked_at)
        self.assertIsNone(pending.revoked_at)
        self.assertTrue(pending.is_pending)
        self.assertTrue(SuiteCredentialHandoff.objects.filter(pk=handoff.pk).exists())
        self.assertIsNone(resolve_service_token(token))
