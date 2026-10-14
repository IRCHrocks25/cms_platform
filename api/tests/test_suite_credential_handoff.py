"""Owner-approved S04 credential handoff: encrypted retry until receipt acknowledgment."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from api.models import ServiceCredential, SuiteCredentialHandoff
from api.service_auth import resolve_service_token, revoke_service_credential
from api.suite_credential_handoff import (
    SuiteCredentialHandoffError,
    acknowledge_suite_credential_handoff,
    begin_suite_credential_handoff,
    purge_expired_suite_handoffs,
)
from core.services.suite_bindings import create_suite_site


KEY = Fernet.generate_key().decode()
HTML = '<section data-section="hero"><h1>Suite</h1></section>'


@override_settings(SUITE_CREDENTIAL_HANDOFF_FERNET_KEY=KEY,
                   SUITE_CREDENTIAL_HANDOFF_TTL_SECONDS=900)
class SuiteCredentialHandoffTests(TestCase):
    def setUp(self):
        user = get_user_model()
        self.operator = user.objects.create_superuser("suite-handoff-operator", "ops@example.test", "x")
        self.owner = user.objects.create_user("suite-handoff-owner", "owner@example.test", "x")
        self.binding = create_suite_site(
            business_id=uuid.uuid4(), operation_key=uuid.uuid4(),
            name="Handoff", subdomain="handoff", owner=self.owner, html=HTML,
            operator=self.operator,
        )
        self.operation_key = uuid.uuid4()

    def begin(self, **changes):
        args = dict(binding_id=self.binding.pk, operation_key=self.operation_key,
                    scopes={"read", "draft"}, operator=self.operator)
        args.update(changes)
        return begin_suite_credential_handoff(**args)

    def ack(self, **changes):
        args = dict(binding_id=self.binding.pk, operation_key=self.operation_key,
                    operator=self.operator)
        args.update(changes)
        return acknowledge_suite_credential_handoff(**args)

    def test_lost_response_replays_only_encrypted_short_lived_pending_credential(self):
        first = self.begin()
        again = self.begin()
        self.assertEqual(first.token, again.token)
        self.assertEqual(first.credential_id, again.credential_id)
        self.assertIsNone(resolve_service_token(first.token))
        self.assertEqual(ServiceCredential.objects.count(), 1)
        handoff = SuiteCredentialHandoff.objects.get(operation_key=self.operation_key)
        self.assertNotIn(first.token, str(handoff.__dict__))
        self.assertNotEqual(handoff.ciphertext, first.token)
        self.assertEqual(handoff.binding_id, self.binding.pk)
        self.assertLessEqual(handoff.expires_at, timezone.now() + timedelta(minutes=15))
        self.assertEqual((first.expires_at - timezone.now()).days, 29)

    def test_ack_erases_secret_activates_token_and_is_idempotent_without_replay(self):
        receipt = self.begin()
        credential = self.ack()
        self.assertEqual(credential.pk, receipt.credential_id)
        self.assertEqual(resolve_service_token(receipt.token).tenant.pk, self.binding.tenant_id)
        handoff = SuiteCredentialHandoff.objects.get(operation_key=self.operation_key)
        self.assertEqual(handoff.ciphertext, "")
        self.assertIsNotNone(handoff.acknowledged_at)
        self.assertEqual(self.ack().pk, credential.pk)
        with self.assertRaises(SuiteCredentialHandoffError):
            self.begin()

    def test_rotation_keeps_old_usable_until_ack_then_revokes_it(self):
        first = self.begin()
        self.ack()
        second_op = uuid.uuid4()
        with self.assertRaises(SuiteCredentialHandoffError):
            self.begin(operation_key=second_op, scopes={"read"}, rotate=True)
        replacement = self.begin(operation_key=second_op, rotate=True)
        self.assertIsNotNone(resolve_service_token(first.token))
        self.assertIsNone(resolve_service_token(replacement.token))
        self.assertNotEqual(first.token, replacement.token)
        acknowledge_suite_credential_handoff(
            binding_id=self.binding.pk, operation_key=second_op, operator=self.operator)
        self.assertIsNone(resolve_service_token(first.token))
        self.assertEqual(resolve_service_token(replacement.token).tenant.pk, self.binding.tenant_id)
        self.assertEqual(ServiceCredential.objects.filter(tenant=self.binding.tenant,
            revoked_at__isnull=True, is_pending=False).count(), 1)

    def test_wrong_binding_operator_scope_and_operation_conflicts_do_not_leak(self):
        other = create_suite_site(
            business_id=uuid.uuid4(), operation_key=uuid.uuid4(),
            name="Other", subdomain="other-handoff", owner=self.owner,
            html=HTML, operator=self.operator,
        )
        with self.assertRaises(SuiteCredentialHandoffError):
            self.begin(operator=self.owner)
        self.assertEqual(ServiceCredential.objects.count(), 0)
        self.begin()
        for changes in (
            {"binding_id": other.pk},
            {"scopes": {"read"}},
            {"rotate": True},
        ):
            with self.subTest(changes=changes), self.assertRaises(SuiteCredentialHandoffError):
                self.begin(**changes)
        with self.assertRaises(SuiteCredentialHandoffError):
            self.begin(operation_key=uuid.uuid4())
        self.assertEqual(ServiceCredential.objects.count(), 1)

    def test_missing_key_fails_closed_before_writes(self):
        with override_settings(SUITE_CREDENTIAL_HANDOFF_FERNET_KEY=""):
            with self.assertRaises(SuiteCredentialHandoffError):
                self.begin()
        self.assertEqual(ServiceCredential.objects.count(), 0)
        self.assertEqual(SuiteCredentialHandoff.objects.count(), 0)

    def test_ack_requires_verified_operator_and_exact_binding(self):
        receipt = self.begin()
        other = create_suite_site(
            business_id=uuid.uuid4(), operation_key=uuid.uuid4(),
            name="Other ack", subdomain="other-ack", owner=self.owner,
            html=HTML, operator=self.operator,
        )
        for changes in ({"operator": self.owner}, {"binding_id": other.pk}):
            with self.subTest(changes=changes), self.assertRaises(SuiteCredentialHandoffError):
                self.ack(**changes)
        self.assertIsNone(resolve_service_token(receipt.token))
        self.assertNotEqual(SuiteCredentialHandoff.objects.get(
            operation_key=self.operation_key).ciphertext, "")

    def test_explicit_expiry_purge_removes_secret_and_revokes_pending(self):
        receipt = self.begin()
        SuiteCredentialHandoff.objects.filter(operation_key=self.operation_key).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(purge_expired_suite_handoffs(), 1)
        self.assertEqual(SuiteCredentialHandoff.objects.get(
            operation_key=self.operation_key).ciphertext, "")
        credential = ServiceCredential.objects.get(pk=receipt.credential_id)
        self.assertIsNotNone(credential.revoked_at)

    def test_invalid_binding_does_not_trigger_global_handoff_cleanup(self):
        self.begin()
        SuiteCredentialHandoff.objects.filter(operation_key=self.operation_key).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(SuiteCredentialHandoffError):
            self.begin(binding_id=None, operation_key=uuid.uuid4())
        self.assertNotEqual(SuiteCredentialHandoff.objects.get(
            operation_key=self.operation_key).ciphertext, "")

    def test_expired_handoff_cannot_replay_or_activate_and_purge_erases_ciphertext(self):
        receipt = self.begin()
        handoff = SuiteCredentialHandoff.objects.get(operation_key=self.operation_key)
        handoff.expires_at = timezone.now() - timedelta(seconds=1)
        handoff.save(update_fields=["expires_at"])
        with self.assertRaises(SuiteCredentialHandoffError):
            self.begin()
        with self.assertRaises(SuiteCredentialHandoffError):
            self.ack()
        self.assertEqual(purge_expired_suite_handoffs(), 0)
        handoff.refresh_from_db()
        self.assertEqual(handoff.ciphertext, "")
        self.assertIsNone(resolve_service_token(receipt.token))
        with self.assertRaises(SuiteCredentialHandoffError):
            self.begin()

    def test_revoked_pending_credential_cannot_be_acked(self):
        receipt = self.begin()
        revoke_service_credential(ServiceCredential.objects.get(pk=receipt.credential_id))
        with self.assertRaises(SuiteCredentialHandoffError):
            self.ack()
        self.assertIsNone(resolve_service_token(receipt.token))


@override_settings(SUITE_CREDENTIAL_HANDOFF_FERNET_KEY=KEY,
                   SUITE_CREDENTIAL_HANDOFF_TTL_SECONDS=900)
class SuiteCredentialHandoffConcurrencyTests(TransactionTestCase):
    def make_binding(self, suffix):
        user = get_user_model()
        operator = user.objects.create_superuser(
            f"handoff-ops-{suffix}", f"ops-{suffix}@example.test", "x")
        owner = user.objects.create_user(
            f"handoff-owner-{suffix}", f"owner-{suffix}@example.test", "x")
        binding = create_suite_site(
            business_id=uuid.uuid4(), operation_key=uuid.uuid4(),
            name=f"Concurrent {suffix}", subdomain=f"concurrent-{suffix}",
            owner=owner, html=HTML, operator=operator,
        )
        return binding, operator

    def test_concurrent_same_operation_returns_one_pending_token(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock integration test")
        binding, operator = self.make_binding("same")
        key = uuid.uuid4()
        start = Barrier(3)

        def begin():
            close_old_connections()
            try:
                start.wait(timeout=5)
                return begin_suite_credential_handoff(
                    binding_id=binding.pk, operation_key=key,
                    scopes={"read", "draft"}, operator=operator)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(begin) for _ in range(2)]
            start.wait(timeout=5)
            one, two = [future.result(timeout=20) for future in futures]
        self.assertEqual(one.token, two.token)
        self.assertEqual(ServiceCredential.objects.filter(tenant=binding.tenant,
            revoked_at__isnull=True, is_pending=True).count(), 1)
        self.assertEqual(SuiteCredentialHandoff.objects.filter(operation_key=key).count(), 1)

    def test_concurrent_different_operations_have_one_winner(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock integration test")
        binding, operator = self.make_binding("different")
        keys = [uuid.uuid4(), uuid.uuid4()]
        start = Barrier(3)

        def begin(key):
            close_old_connections()
            try:
                start.wait(timeout=5)
                try:
                    receipt = begin_suite_credential_handoff(
                        binding_id=binding.pk, operation_key=key,
                        scopes={"read", "draft"}, operator=operator)
                    return ("issued", receipt.credential_id)
                except SuiteCredentialHandoffError:
                    return ("conflict", None)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(begin, key) for key in keys]
            start.wait(timeout=5)
            results = [future.result(timeout=20) for future in futures]
        self.assertCountEqual([kind for kind, _ in results], ["issued", "conflict"])
        self.assertEqual(ServiceCredential.objects.filter(tenant=binding.tenant,
            revoked_at__isnull=True, is_pending=True).count(), 1)
        self.assertEqual(SuiteCredentialHandoff.objects.filter(binding=binding).count(), 1)

    def test_concurrent_ack_activates_once(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock integration test")
        binding, operator = self.make_binding("ack")
        key = uuid.uuid4()
        receipt = begin_suite_credential_handoff(
            binding_id=binding.pk, operation_key=key,
            scopes={"read", "draft"}, operator=operator)
        start = Barrier(3)

        def ack():
            close_old_connections()
            try:
                start.wait(timeout=5)
                return acknowledge_suite_credential_handoff(
                    binding_id=binding.pk, operation_key=key, operator=operator).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(ack) for _ in range(2)]
            start.wait(timeout=5)
            ids = [future.result(timeout=20) for future in futures]
        self.assertEqual(ids, [receipt.credential_id, receipt.credential_id])
        self.assertEqual(ServiceCredential.objects.filter(tenant=binding.tenant,
            revoked_at__isnull=True, is_pending=False).count(), 1)
        self.assertEqual(resolve_service_token(receipt.token).tenant.pk, binding.tenant_id)
