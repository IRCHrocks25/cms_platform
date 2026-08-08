# CMS-20 Restrict OAuth Grants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Advertise and accept only `authorization_code` + `refresh_token` (with PKCE/S256); refuse password and implicit grants.

**Architecture:** Configure django-oauth-toolkit via `OAUTH2_PROVIDER` only: narrow `OAUTH2_GRANT_TYPES_SUPPORTED` / `OAUTH2_RESPONSE_TYPES_SUPPORTED`, and enable RFC 9700 gates `COMPLIANT_BCP_RFC9700_PASSWORD_GRANT` and `COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT` so the token/authorize endpoints refuse those grants even for Applications registered for them. No custom metadata view.

**Tech Stack:** Django 5.1, django-oauth-toolkit 3.4.0, Django test runner, Python 3.12

**Diagnosis (step one evidence):** Against an Application with `authorization_grant_type=password`, `POST /token/` with `grant_type=password` returns **200** and an access token (case **b** — genuinely enabled at server level). Against the configured Claude `authorization-code` client it returns `unauthorized_client` (per-app restriction only). Same pattern for implicit: an `implicit` Application reaches the consent screen; auth-code app gets `unauthorized_client`.

---

### Task 1: Failing tests for metadata + grant refusal

**Files:**
- Create: `api/tests/test_oauth_grants.py`
- Modify: `core/tests/test_api_scaffold.py` (optional assertion tighten — prefer dedicated file)

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/test_oauth_grants.py — metadata lists only auth_code+refresh;
# password grant against a password Application is rejected;
# implicit authorize against an implicit Application is rejected;
# auth_code+PKCE consent still yields a code (reuse pattern from test_consent).
```

- [ ] **Step 2: Run tests — expect RED**

Run: `.venv/bin/python manage.py collectstatic --noinput && .venv/bin/python manage.py test api.tests.test_oauth_grants -v2`

- [ ] **Step 3: Update `cms_platform/settings.py` OAUTH2_PROVIDER**

```python
OAUTH2_PROVIDER = {
    "DCR_ENABLED": False,
    "PKCE_REQUIRED": True,
    "COMPLIANT_BCP_RFC9700_PKCE_METHOD": True,
    "COMPLIANT_BCP_RFC9700_PKCE_REQUIRED": True,
    "COMPLIANT_BCP_RFC9700_PASSWORD_GRANT": True,
    "COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT": True,
    "OAUTH2_GRANT_TYPES_SUPPORTED": [
        "authorization_code",
        "refresh_token",
    ],
    "OAUTH2_RESPONSE_TYPES_SUPPORTED": ["code"],
}
```

- [ ] **Step 4: Run tests — expect GREEN**

- [ ] **Step 5: Run full suite on Python 3.12 — expect 574+**

- [ ] **Step 6: Commit, push, open PR stating case (b), move Plane → In Review**
