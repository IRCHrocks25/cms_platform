# S04 Suite Credential Handoff Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a default-off, retryable, encrypted 30-day Sites service credential handoff for a verified suite tenant binding.

**Architecture:** A privileged internal service creates a pending, tenant-scoped credential and stores its plaintext only as a short-lived Fernet ciphertext. The same immutable operation can replay the ciphertext until mAIstro acknowledges it. Acknowledgment atomically activates the pending credential, revokes the previous active credential during rotation, and erases ciphertext. No HTTP route, scheduled job, public effect, or production key is added.

**Tech Stack:** Django 5.1, PostgreSQL row locks and partial unique indexes, `cryptography.Fernet`, existing S03 service credentials and S04 suite bindings.

---

## Testing / acceptance evidence (defined before code)

The pre-implementation plan is on KPILOT-379. Failing-first tests cover create and lost-response replay, tenant/operator/scope conflicts, ciphertext at rest, missing key fail closed, pending-token denial, acknowledgment erasure/idempotence, rotation overlap and old-key revocation, expiry and cleanup, plus PostgreSQL concurrency. S03/S04 regressions, full local SQLite, migration check, and exact-head hosted CI are required. Browser staging and real installed-account checks remain UNRUN until deployed; KPILOT-379 stays open.

## Task 1: Failing-first contract tests

**Files:** Create `api/tests/test_suite_credential_handoff.py`.

1. Build a real `SuiteTenantBinding` through `create_suite_site`, with a dedicated test Fernet key.
2. Assert one operation returns the same plaintext on replay, ciphertext never equals plaintext, and a pending token cannot resolve.
3. Assert wrong business/binding/operator/scopes and missing key fail without credential or handoff rows.
4. Assert acknowledgment removes ciphertext and activates token; a replay after acknowledgment returns no plaintext.
5. Assert rotation leaves old token live before ack and revokes it on ack; expired/revoked pending tokens cannot activate.
6. Run `PYTHONPATH=/tmp/cms-s04-deps python manage.py test api.tests.test_suite_credential_handoff` under the shared lock and record the expected red assertions.

## Task 2: Durable pending and handoff state

**Files:** Modify `api/models.py`, `api/service_auth.py`, `cms_platform/settings.py`; create `api/migrations/0003_suite_credential_handoff.py` and `api/suite_credential_handoff.py`.

1. Add `is_pending` to `ServiceCredential`. Replace the one-active constraint with separate one-active and one-pending constraints per tenant; existing rows default active. The resolver rejects pending tokens. Existing immediate issue/rotate and revoke remain compatible, but refuse unsafe overlap with a pending handoff.
2. Add `SuiteCredentialHandoff`: immutable operation UUID, FK to the suite binding and pending credential, encrypted token, expiry, acknowledgment timestamp, and optional previous credential FK. Keep the metadata for replay denial after ciphertext erasure.
3. Use a separate `SUITE_CREDENTIAL_HANDOFF_FERNET_KEY` setting, empty by default. Validate it before writes; missing/invalid key fails closed. A 15-minute retrieval TTL is a conservative operational default, overridable in settings for tests and deployment review.
4. Under binding and tenant row locks, begin/retry the operation, clean expired ciphertext and pending credentials, and refuse conflicting operations. Acknowledge under the same locks, revoke old active token before activating replacement, erase ciphertext, and allow idempotent ack without returning the token.
5. Add a callable purge for expired ciphertext/pending credentials; no scheduler is wired in this default-off PR.

## Task 3: Verify and hand off

1. Run the new tests and S03/S04 regression suites, `makemigrations --check --dry-run`, collectstatic, then full Django suite under the shared build lock.
2. Run isolated PostgreSQL concurrency and handoff tests with a disposable database. Destroy it afterward.
3. Open a stacked PR against `feat/s04-sites-suite-provisioning`; record exact-head CI and Testing / acceptance evidence on PR and Plane. Keep browser, staging and installed-account checks UNRUN. Do not merge or deploy.
