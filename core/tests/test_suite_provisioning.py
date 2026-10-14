"""S04: suite tenant create/link is immutable, replay-safe and default-off."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from core.models import SuiteFormBinding, SuiteTenantBinding, Template, Tenant
from core.services.suite_bindings import SuiteBindingConflict, create_suite_site, link_suite_site


User = get_user_model()
HTML = '<section data-section="hero"><h1>Hello</h1></section>'


class SuiteProvisioningTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_superuser("suite-operator", "ops@example.test", "x")
        self.owner = User.objects.create_user("suite-owner", "owner@example.test", "x")
        self.other_owner = User.objects.create_user("other-owner", "other@example.test", "x")
        self.business_id = uuid.uuid4()
        self.operation_key = uuid.uuid4()

    def create(self, **changes):
        args = dict(
            business_id=self.business_id,
            operation_key=self.operation_key,
            name="Support Demo",
            subdomain="support-demo",
            owner=self.owner,
            html=HTML,
            operator=self.operator,
        )
        args.update(changes)
        return create_suite_site(**args)

    def test_create_replay_keeps_one_draft_tenant_owner_and_inactive_form_slot(self):
        user_count = User.objects.count()
        first = self.create()
        second = self.create()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(SuiteTenantBinding.objects.count(), 1)
        self.assertEqual(SuiteFormBinding.objects.count(), 1)
        self.assertEqual(User.objects.count(), user_count)
        self.assertEqual(first.tenant.owner_id, self.owner.pk)
        self.assertEqual(first.confirmed_by_id, self.operator.pk)
        self.assertFalse(first.tenant.is_published)
        self.assertFalse(first.form_binding.is_active)
        self.assertEqual(first.tenant.template.tenant_id, first.tenant_id)
        self.assertTrue(first.tenant.memberships.filter(user=self.owner, role="owner").exists())

    def test_create_replay_with_changed_input_is_refused_without_side_effects(self):
        self.create()
        for changed in (
            {"name": "Different"},
            {"html": "<h1>Other</h1>"},
            {"owner": self.other_owner},
            {"operation_key": uuid.uuid4()},
            {"business_id": uuid.uuid4()},
        ):
            with self.subTest(changed=changed), self.assertRaises(SuiteBindingConflict):
                self.create(**changed)
        self.assertEqual(Tenant.objects.count(), 1)

    def test_slug_collision_never_adopts_another_business(self):
        self.create()
        with self.assertRaises(SuiteBindingConflict):
            self.create(business_id=uuid.uuid4(), operation_key=uuid.uuid4())
        self.assertEqual(Tenant.objects.count(), 1)

    def test_unbound_existing_slug_cannot_be_adopted_by_create(self):
        template = Template.objects.create(name="Existing", html_source=HTML)
        Tenant.objects.create(
            name="Another business", subdomain="support-demo",
            template=template, owner=self.other_owner,
        )
        with self.assertRaises(SuiteBindingConflict):
            self.create()
        self.assertEqual(SuiteTenantBinding.objects.count(), 0)
        self.assertEqual(Tenant.objects.count(), 1)

    def test_replay_refuses_drifted_local_owner(self):
        binding = self.create()
        binding.tenant.owner = self.other_owner
        binding.tenant.save(update_fields=["owner"])
        with self.assertRaises(SuiteBindingConflict):
            self.create()

    def test_bound_tenant_cannot_be_deleted_and_recreated_under_same_business(self):
        binding = self.create()
        with self.assertRaises(ProtectedError):
            binding.tenant.delete()
        self.assertTrue(Tenant.objects.filter(pk=binding.tenant_id).exists())
        self.assertTrue(SuiteTenantBinding.objects.filter(pk=binding.pk).exists())

    def test_link_requires_exact_tenant_owner_and_operator_confirmation(self):
        template = Template.objects.create(name="Existing", html_source=HTML)
        tenant = Tenant.objects.create(
            name="Katek", subdomain="katek", template=template, owner=self.owner,
            is_published=True,
        )
        args = dict(
            business_id=self.business_id,
            operation_key=self.operation_key,
            tenant_id=tenant.pk,
            expected_owner_id=self.owner.pk,
            operator=self.operator,
        )
        with self.assertRaises(SuiteBindingConflict):
            link_suite_site(**{**args, "expected_owner_id": self.other_owner.pk})
        with self.assertRaises(SuiteBindingConflict):
            link_suite_site(**{**args, "operator": self.owner})
        before_users = User.objects.count()
        binding = link_suite_site(**args)
        self.assertEqual(binding.pk, link_suite_site(**args).pk)
        self.assertEqual(binding.tenant_id, tenant.pk)
        self.assertEqual(binding.confirmed_by_id, self.operator.pk)
        self.assertFalse(binding.form_binding.is_active)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(User.objects.count(), before_users)
        self.assertTrue(tenant.is_published)

    def test_linked_tenant_cannot_be_rebound_to_other_business(self):
        template = Template.objects.create(name="Existing", html_source=HTML)
        tenant = Tenant.objects.create(name="Katek", subdomain="katek", template=template, owner=self.owner)
        args = dict(tenant_id=tenant.pk, expected_owner_id=self.owner.pk, operator=self.operator)
        link_suite_site(business_id=self.business_id, operation_key=self.operation_key, **args)
        with self.assertRaises(SuiteBindingConflict):
            link_suite_site(business_id=uuid.uuid4(), operation_key=uuid.uuid4(), **args)
        self.assertEqual(SuiteTenantBinding.objects.count(), 1)

    def test_create_requires_operator_and_rolls_back_invalid_seed(self):
        with self.assertRaises(SuiteBindingConflict):
            self.create(operator=self.owner)
        with self.assertRaises(SuiteBindingConflict):
            self.create(operator=object())
        with self.assertRaises(ValueError):
            self.create(html="  ")
        self.assertEqual(Tenant.objects.count(), 0)
        self.assertEqual(SuiteTenantBinding.objects.count(), 0)


class SuiteProvisioningConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_same_operation_creates_one_tenant(self):
        operator = User.objects.create_superuser("operator", "ops@example.test", "x")
        owner = User.objects.create_user("owner", "owner@example.test", "x")
        business_id, operation_key = uuid.uuid4(), uuid.uuid4()
        ready = Barrier(2)

        def attempt():
            close_old_connections()
            try:
                ready.wait(timeout=5)
                return create_suite_site(
                    business_id=business_id, operation_key=operation_key,
                    name="Parallel", subdomain="parallel-site", owner=owner,
                    html=HTML, operator=operator,
                ).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            ids = [future.result(timeout=20) for future in futures]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(SuiteTenantBinding.objects.count(), 1)
        self.assertEqual(SuiteFormBinding.objects.count(), 1)
