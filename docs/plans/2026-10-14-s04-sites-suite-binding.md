# S04 Sites Suite Binding Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Sites tenant creation and verified linking replay-safe for a privileged suite provisioner without activating a public entrypoint or issuing a credential.

**Architecture:** A service accepts immutable global business and operation UUIDs. It creates an unpublished tenant with an existing local owner, or links an exact tenant only after an operator confirms its current owner. A unique binding and inactive form slot commit in one database transaction. The future provisioner HTTP adapter, service-key handoff, and public tools remain unwired pending separate decisions and acceptance.

**Tech Stack:** Django 5.1, SQLite targeted tests, disposable PostgreSQL integration.

**Approved sources:** IBC suite design and S04 implementation plan dated 2026-10-12; Support Demo cross-environment design dated 2026-10-14. This is their design-independent, default-off S04 slice.

---

## Task 1: Define red acceptance cases

**Files:** Create `core/tests/test_suite_provisioning.py`.

1. Add tests for same-key replay returning one tenant/slot, conflicting business/key/subdomain rejection, exact verified link without tenant creation, owner mismatch and non-operator denial, no owner password creation, unpublished tenant, inactive form slot, and atomic rollback on seed error.
2. Run under `flock ~/.cache/katalyst-build-gate.lock` using `python3 manage.py test core.tests.test_suite_provisioning`. Expect failures from missing model/service, not setup failures.

## Task 2: Add durable binding schema

**Files:** Modify `core/models.py`; create next `core/migrations/NNNN_suite_binding.py` after fetching main and checking leaf.

1. Add `SuiteTenantBinding`: unique business UUID, operation UUID, protected one-to-one tenant, immutable creation mode, owner FK snapshot, operator confirmation. Add `SuiteFormBinding`: one-to-one binding, default disabled; no outbound delivery target or active public form behavior.
2. Generate/check migration and run the targeted tests again. Expected remaining failures concern service logic.

## Task 3: Reuse atomic tenant seeding and implement service

**Files:** Modify `core/services/accounts.py`; create `core/services/suite_bindings.py`.

1. Add an existing-owner path to `create_tenant_account`: require saved owner, skip user/password creation, retain atomic template clone and owner membership; keep old arguments and behavior for existing callers.
2. Implement create/link service with strict UUID validation, normalized exact subdomain, tenant/owner lookup by ID, operator superuser confirmation for link, and transactional one-to-one/unique writes. Replays must verify request identity; conflicting replay raises a domain error. Catch uniqueness races and reread only matching committed bindings. Never match by name or email.
3. Run targeted tests under build lock until green, then `api.tests.test_mcp_create_client` to prove the existing superuser MCP flow is unchanged.

## Task 4: Verify and hand off

1. Run `python manage.py makemigrations --check --dry-run`, targeted tests, full Django suite, and disposable PostgreSQL concurrent/replay integration as available, all under the shared build lock. Record exact commands/results.
2. Keep browser and installed-account acceptance UNRUN until exact reviewed commit is deployed, dedicated test tenant IDs are verified, and the owner resolves credential recovery/lifetime. Keep KPILOT-379 open.
3. Open an isolated PR with a Testing / acceptance evidence section and explicit default-off boundary. Do not merge or deploy.
