"""PostgreSQL regression for service draft writes racing publication."""

from threading import Barrier, Event, Thread
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from api.mcp.tools import HANDLERS, call_tool
from api.mcp.errors import tool_success
from api.service_auth import issue_service_credential, resolve_service_token
from core.models import Page, Template, Tenant


class ServiceDraftPublicationLockTests(TransactionTestCase):
    def test_concurrent_issuance_has_one_winner(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock integration test")

        owner = get_user_model().objects.create_user("s03-issue-owner", "s03-issue@example.test", "x")
        template = Template.objects.create(name="S03 issue", html_source="<p>home</p>")
        tenant = Tenant.objects.create(name="S03 issue", subdomain="s03-issue", template=template, owner=owner)
        start = Barrier(3)
        outcomes = []

        def issue():
            close_old_connections()
            try:
                start.wait(5)
                credential, _ = issue_service_credential(tenant, scopes={"read"})
                outcomes.append(("issued", credential.pk))
            except ValueError:
                outcomes.append(("duplicate", None))
            finally:
                close_old_connections()

        workers = [Thread(target=issue) for _ in range(2)]
        for worker in workers:
            worker.start()
        start.wait(5)
        for worker in workers:
            worker.join(5)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertCountEqual([kind for kind, _ in outcomes], ["issued", "duplicate"])
        self.assertEqual(tenant.service_credentials.filter(revoked_at__isnull=True).count(), 1)

    def test_publication_waits_for_checked_draft_write(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock integration test")

        owner = get_user_model().objects.create_user("s03-lock-owner", "s03-lock@example.test", "x")
        template = Template.objects.create(name="S03 lock home", html_source="<p>home</p>")
        tenant = Tenant.objects.create(name="S03 lock", subdomain="s03-lock", template=template, owner=owner)
        page_template = Template.objects.create(name="S03 lock page", html_source="<p>draft</p>", tenant=tenant)
        page = Page.objects.create(tenant=tenant, template=page_template, slug="draft", title="Draft")
        _, token = issue_service_credential(tenant, scopes={"draft"})
        principal = resolve_service_token(token)
        entered = Event()
        release = Event()
        publishing = Event()
        published = Event()
        errors = []

        def stalled_handler(_auth, **_args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("draft handler was not released")
            return tool_success({"ok": True})

        def writer():
            close_old_connections()
            try:
                call_tool(principal, "push_page", {"site": tenant.subdomain, "page": "draft", "html": "<p>new</p>"})
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def publisher():
            close_old_connections()
            try:
                publishing.set()
                Page.objects.filter(pk=page.pk).update(is_published=True)
                published.set()
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch.dict(HANDLERS, {"push_page": stalled_handler}):
            write_thread = Thread(target=writer)
            publish_thread = Thread(target=publisher)
            write_thread.start()
            try:
                self.assertTrue(entered.wait(5), "draft handler did not start")
                publish_thread.start()
                self.assertTrue(publishing.wait(5), "publisher did not start")
                self.assertFalse(published.wait(0.25), "publication bypassed draft row lock")
            finally:
                release.set()
                write_thread.join(5)
                if publish_thread.ident is not None:
                    publish_thread.join(5)

        self.assertFalse(errors)
        self.assertTrue(published.is_set())
        result, _ = call_tool(principal, "push_page", {"site": tenant.subdomain, "page": "draft", "html": "<p>again</p>"})
        self.assertTrue(result["isError"])
