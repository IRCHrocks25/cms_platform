# S04 bound owner transfer guard implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Keep an immutable Suite tenant binding usable by refusing an owner transfer that would invalidate it.

**Architecture:** The CMS-82 transfer service already locks the tenant before it reads memberships. Check for a SuiteTenantBinding under that lock and raise OwnerTransferError before changing the canonical owner or deleting the former owner's membership. Preserve the S04 binding's separate owner-drift rejection for changes outside this service.

**Tech Stack:** Django 5.1, PostgreSQL row locks, Django TestCase and TransactionTestCase.

---

### Task 1: Failing-first combined regression

**Files:** Modify `core/tests/test_suite_provisioning.py`.

1. Add a CMS-82 owner transfer test for both a created and linked S04 binding with two active Owner memberships.
2. Assert transfer raises OwnerTransferError and leaves tenant owner, memberships, immutable binding and inactive form slot unchanged. Assert exact binding replay still works.
3. Run the single test on SQLite and capture its expected failure before implementation.

### Task 2: Minimal fail-closed guard

**Files:** Modify `core/services/accounts.py`.

1. After locking the tenant in `transfer_and_remove_owner`, check whether a SuiteTenantBinding exists for that tenant. Raise OwnerTransferError before any owner or membership write.
2. Run the new test and `core.tests.test_owner_transfer` to confirm the bound denial and ordinary unbound transfer behavior.
3. Run S04 provisioning, account service, tenant deletion and S03 auth focused suites.

### Task 3: Release gates and evidence

1. Run `ruff check`, `git diff --check`, `python manage.py makemigrations --check --dry-run`, and full SQLite suite after `collectstatic`.
2. Run focused S04/CMS-82/S03 tests on an isolated disposable PostgreSQL 18 database; remove the container and data volume afterward.
3. Commit, push the rebased #69 branch with a guarded lease, check hosted CI at the exact head, and update PR #69 and KPILOT-379 Testing / acceptance evidence. Leave #71/#70 and production untouched.
