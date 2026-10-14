"""CMS-82: safely leave one canonical Owner after removing the other."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Template, Tenant, TenantMembership
from core.services.accounts import OwnerTransferError, transfer_and_remove_owner


User = get_user_model()


@override_settings(TENANT_BASE_DOMAIN="localhost")
class OwnerTransferTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("agency", password="x", is_staff=True)
        self.alice = User.objects.create_user("alice", password="x")
        self.bob = User.objects.create_user("bob", password="x")
        self.template = Template.objects.create(name="Bare", html_source="<html></html>")
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.template,
            owner=self.alice,
        )
        self.alice_membership = TenantMembership.objects.create(
            tenant=self.tenant, user=self.alice, role=TenantMembership.ROLE_OWNER,
        )
        self.bob_membership = TenantMembership.objects.create(
            tenant=self.tenant, user=self.bob, role=TenantMembership.ROLE_OWNER,
        )

    def _transfer(self, successor=None):
        return transfer_and_remove_owner(
            self.tenant.pk, self.alice_membership.pk,
            (successor or self.bob_membership).pk, actor_id=self.staff.pk,
        )

    def _assert_unchanged(self):
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.owner_id, self.alice.pk)
        self.assertTrue(TenantMembership.objects.filter(pk=self.alice_membership.pk).exists())

    def _agency_client(self, user=None):
        client = Client(HTTP_HOST="localhost")
        client.force_login(user or self.staff)
        return client

    def test_service_transfers_and_removes_atomically(self):
        successor = self._transfer()
        self.tenant.refresh_from_db()
        self.assertEqual(successor.pk, self.bob.pk)
        self.assertEqual(self.tenant.owner_id, self.bob.pk)
        self.assertFalse(TenantMembership.objects.filter(pk=self.alice_membership.pk).exists())
        self.assertTrue(TenantMembership.objects.filter(
            pk=self.bob_membership.pk, role=TenantMembership.ROLE_OWNER,
        ).exists())
        self.assertTrue(User.objects.filter(pk=self.alice.pk).exists())
        self.assertTrue(self.tenant.user_can_edit(self.bob))
        self.assertFalse(self.tenant.user_can_edit(self.alice))

    def test_refuses_missing_extra_inactive_or_wrong_successor(self):
        self.bob_membership.delete()
        with self.assertRaises(OwnerTransferError):
            self._transfer()
        self._assert_unchanged()

        self.bob_membership = TenantMembership.objects.create(
            tenant=self.tenant, user=self.bob, role=TenantMembership.ROLE_OWNER,
        )
        carol = User.objects.create_user("carol", password="x")
        carol_membership = TenantMembership.objects.create(
            tenant=self.tenant, user=carol, role=TenantMembership.ROLE_OWNER,
        )
        with self.assertRaises(OwnerTransferError):
            self._transfer()
        self._assert_unchanged()
        carol_membership.delete()

        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])
        with self.assertRaises(OwnerTransferError):
            self._transfer()
        self._assert_unchanged()
        self.bob.is_active = True
        self.bob.save(update_fields=["is_active"])

        editor = User.objects.create_user("editor", password="x")
        editor_membership = TenantMembership.objects.create(
            tenant=self.tenant, user=editor, role=TenantMembership.ROLE_EDITOR,
        )
        with self.assertRaises(OwnerTransferError):
            self._transfer(editor_membership)
        self._assert_unchanged()

    def test_refuses_wrong_target_or_missing_canonical_membership(self):
        with self.assertRaises(OwnerTransferError):
            transfer_and_remove_owner(
                self.tenant.pk, self.bob_membership.pk,
                self.alice_membership.pk, actor_id=self.staff.pk,
            )
        self._assert_unchanged()
        self.alice_membership.delete()
        with self.assertRaises(OwnerTransferError):
            self._transfer()
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.owner_id, self.alice.pk)

    def test_delete_failure_rolls_back_owner_change(self):
        with patch.object(TenantMembership, "delete", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self._transfer()
        self._assert_unchanged()

    def test_agency_page_names_successor_and_route_transfers(self):
        client = self._agency_client()
        detail = client.get(reverse("dashboard:tenant_detail", args=[self.tenant.pk]))
        self.assertContains(detail, "Transfer to bob and remove alice")
        url = reverse("dashboard:tenant_member_transfer_remove", args=[
            self.tenant.pk, self.alice_membership.pk,
        ])
        self.assertEqual(client.get(url).status_code, 405)
        response = client.post(url, {"successor_membership_id": self.bob_membership.pk})
        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.owner_id, self.bob.pk)
        self.assertFalse(TenantMembership.objects.filter(pk=self.alice_membership.pk).exists())

    def test_agency_transfer_rejects_nonstaff_and_stale_choice(self):
        url = reverse("dashboard:tenant_member_transfer_remove", args=[
            self.tenant.pk, self.alice_membership.pk,
        ])
        self.assertEqual(self._agency_client(self.bob).post(
            url, {"successor_membership_id": self.bob_membership.pk},
        ).status_code, 403)
        self._assert_unchanged()
        response = self._agency_client().post(url, {"successor_membership_id": "999999"})
        self.assertEqual(response.status_code, 302)
        self._assert_unchanged()
        csrf_client = Client(HTTP_HOST="localhost", enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(
            url, {"successor_membership_id": self.bob_membership.pk},
        ).status_code, 403)
        self._assert_unchanged()

    def test_single_owner_page_does_not_offer_transfer_or_role_change(self):
        self.bob_membership.delete()
        response = self._agency_client().get(reverse(
            "dashboard:tenant_detail", args=[self.tenant.pk],
        ))
        self.assertContains(response, "Transfer requires exactly two active Owner members.")
        self.assertNotContains(response, "Transfer to bob and remove alice")
        self.assertContains(response, "Owner · Site owner")
        user_page = self._agency_client().get(reverse(
            "dashboard:user_detail", args=[self.alice.pk],
        ))
        self.assertContains(user_page, "Manage site owner")

    def test_secondary_owner_removal_and_canonical_role_guard(self):
        client = self._agency_client()
        role_url = reverse("dashboard:tenant_member_role", args=[
            self.tenant.pk, self.alice_membership.pk,
        ])
        self.assertEqual(client.post(role_url, {"role": "editor"}).status_code, 302)
        self.alice_membership.refresh_from_db()
        self.assertEqual(self.alice_membership.role, TenantMembership.ROLE_OWNER)
        remove_url = reverse("dashboard:tenant_member_remove", args=[
            self.tenant.pk, self.bob_membership.pk,
        ])
        self.assertEqual(client.post(remove_url).status_code, 302)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.owner_id, self.alice.pk)
        self.assertFalse(TenantMembership.objects.filter(pk=self.bob_membership.pk).exists())

    def test_site_team_still_blocks_self_and_canonical_removal(self):
        client = Client(HTTP_HOST="acme.localhost")
        client.force_login(self.alice)
        response = client.post(reverse(
            "dashboard:team_member_remove_self", args=[self.alice_membership.pk],
        ))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TenantMembership.objects.filter(pk=self.alice_membership.pk).exists())
        client.force_login(self.bob)
        response = client.post(reverse(
            "dashboard:team_member_remove_self", args=[self.alice_membership.pk],
        ))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TenantMembership.objects.filter(pk=self.alice_membership.pk).exists())
        client.force_login(self.alice)
        # The canonical owner can remove the *other* Owner through Team.
        response = client.post(reverse(
            "dashboard:team_member_remove_self", args=[self.bob_membership.pk],
        ))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TenantMembership.objects.filter(pk=self.bob_membership.pk).exists())
