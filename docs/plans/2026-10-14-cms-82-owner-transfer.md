# CMS-82 Owner Transfer and Removal Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give agency operators a safe, explicit way to remove a canonical site owner when one other active Owner can replace them.

**Architecture:** A transaction-backed account service validates the two-owner state and performs the owner FK update plus membership delete. A dedicated agency POST and site-detail action call the service. Existing Team and secondary-member removal behavior stays in place; the agency role route also preserves the canonical Owner invariant.

**Tech Stack:** Django ORM, transactions, server-rendered templates, Django TestCase.

---

### Task 1: Define service behavior with failing tests

**Files:**
- Create: `core/tests/test_owner_transfer.py`
- Modify: `core/services/accounts.py`

1. Build a tenant with canonical Owner A and secondary Owner B. Write a test for `transfer_and_remove_owner(tenant_id, target_membership_id, successor_membership_id)` that leaves `Tenant.owner=B`, removes A's membership, retains B as Owner, and leaves A's user account intact.
2. Add refusal cases for one owner, three owners, inactive successor, target not canonical, stale/forged successor, and a missing canonical membership. Assert the database remains unchanged.
3. Mock deletion failure after the owner FK update and assert the whole transaction rolls back.
4. Run `python manage.py test core.tests.test_owner_transfer --noinput` and confirm red, then implement the minimal service with row locks and transaction.atomic. Re-run green and commit.

### Task 2: Expose a clear agency action

**Files:**
- Modify: `dashboard/urls.py`
- Modify: `dashboard/views.py`
- Modify: `templates/dashboard/tenant_detail.html`
- Modify: `core/tests/test_owner_transfer.py`

1. Write failing route tests for agency-only POST, CSRF/GET rejection, confirmation data, and success/error feedback. Assert a secondary Owner still uses the ordinary Remove action. Assert the site Team cannot self-remove or remove the canonical owner.
2. Add `tenant_member_transfer_remove` under the agency site member URLs. Pass the expected successor membership ID from the form to the service. Show “Transfer to B and remove A” on the canonical row only when the current two-owner state qualifies; name both users in the confirmation.
3. Hide the misleading ordinary Remove action for canonical owners and show explanatory text when transfer is unavailable. Guard the agency role route from downgrading the canonical Owner; render its role as fixed. Route ordinary removal and role updates through transaction-backed services that recheck ownership under the same tenant lock, including agency user detail and tenant Team paths.
4. Run targeted tests red/green and commit.

### Task 3: Verify and hand off

**Files:**
- Update CMS-82 Plane testing evidence and comment (external).

1. Run owner-transfer, agency-admin, and scoped-login suites plus `python manage.py check` in the isolated worktree. Run the frontend build if a UI asset change requires it. Record commands, environment, and observed results.
2. Review diff, commit, push, open PR, and move CMS-82 to In Review with live acceptance explicit. Do not mark Done.
3. After deployment and identification of the affected installed account, reproduce the original actor/target/path and verify the authorized two-to-one transition, role display, login access, and site content. Keep the ticket open until all required live checks pass.
