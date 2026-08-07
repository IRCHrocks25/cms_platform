# CMS API Scaffold Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a routable django-ninja API, django-oauth-toolkit authorization server, and reproducible static Claude OAuth client registration.

**Architecture:** Install django-ninja 1.6.2 and django-oauth-toolkit 3.4.0 as pinned application dependencies. Mount django-oauth-toolkit at the origin root so its native RFC 8414 and RFC 9728 metadata routes resolve correctly, and mount the Ninja API under `/api/`. Register the single confidential Claude client through an idempotent management command whose client ID, secret, and redirect URIs come from environment variables.

**Tech Stack:** Django 5.1, django-ninja 1.6.2, django-oauth-toolkit 3.4.0, Django test runner

---

## Design decisions

- django-oauth-toolkit 3.4.0 natively implements RFC 9728 protected-resource metadata and RFC 8707 resource indicators/audience validation, so no custom metadata view is needed.
- PKCE is enforced through django-oauth-toolkit's global `PKCE_REQUIRED=True` setting. The toolkit has no per-application PKCE flag.
- Dynamic client registration remains explicitly disabled.
- The static client command requires credentials through environment variables and never prints the client secret. This keeps credentials out of Git while allowing dev and production to use intentionally provisioned values.
- The provider's own migration files ship inside the pinned package. Verification uses `showmigrations oauth2_provider`; copying third-party migrations into this repository would create an unsupported fork.

### Task 1: Specify API and OAuth routing

**Files:**
- Create: `api/tests/test_scaffold.py`
- Create: `api/__init__.py`
- Create: `api/apps.py`
- Create: `api/api.py`
- Modify: `cms_platform/settings.py`
- Modify: `cms_platform/urls.py`
- Modify: `requirements.txt`

1. Write tests that require `api` and `oauth2_provider` in installed apps, a JSON response from `/api/health`, OAuth authorization-server metadata, RFC 9728 protected-resource metadata, RFC 8707 model support, and disabled DCR.
2. Run the focused tests and confirm they fail because the app and routes do not exist.
3. Add the pinned dependencies, API app, OAuth settings, and URL mounts.
4. Run the focused tests and confirm they pass.

### Task 2: Specify reproducible Claude client registration

**Files:**
- Create: `api/management/__init__.py`
- Create: `api/management/commands/__init__.py`
- Create: `api/management/commands/register_claude_oauth_client.py`
- Modify: `api/tests/test_scaffold.py`
- Modify: `.env.example`

1. Write tests for missing configuration, idempotent registration, confidential client type, authorization-code grant, configured redirect URIs, hashed-at-rest secret, and global PKCE enforcement.
2. Run the focused tests and confirm they fail because the command does not exist.
3. Implement the command using environment-backed Django settings and `update_or_create`, without logging secret material.
4. Document the required environment variables and command in `.env.example`.
5. Run the focused tests and confirm they pass.

### Task 3: Verify and ship

**Files:**
- Modify as required by verification only.

1. Run `python manage.py makemigrations --check --dry-run`.
2. Run `python manage.py showmigrations oauth2_provider`.
3. Run `python manage.py check`.
4. Run `python manage.py collectstatic --noinput`.
5. Run the full Django test suite.
6. Review the diff for scope, secrets, and forbidden-file changes.
7. Commit atomically with conventional commit messages.
8. Push the branch, open one PR covering CMS-1 and CMS-3, comment the PR on both tickets, and move both tickets to In Review.
