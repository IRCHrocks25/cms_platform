# CMS-55 Public Homepage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Serve a public Katek CMS marketing homepage to anonymous visitors on the bare CMS domain without changing any tenant or authenticated-user routing behavior.

**Architecture:** Add one standalone Django template ported from the owner-pinned KATEK AI HTML page. Change only the final branch of `core.views.root_redirect` to render it, and cover every branch of that function with integration tests through Django's test client and tenant middleware.

**Tech Stack:** Django 5.1 templates and test client, plain HTML, inline CSS, vanilla JavaScript

---

### Task 1: Add root redirect coverage

**Files:**
- Create: `core/tests/test_root_redirect.py`

**Step 1: Write the five route tests**

Create fixtures with unique public and draft sentinels. Cover anonymous bare
host, authenticated bare host, anonymous unpublished tenant, published tenant,
and editor-owned unpublished tenant.

**Step 2: Run the homepage test to verify red**

Run:

```bash
python manage.py test core.tests.test_root_redirect.RootRedirectTests.test_anonymous_base_domain_renders_public_homepage
```

Expected: FAIL because the response is still a login redirect.

**Step 3: Run the other four tests against the unchanged function**

Expected: PASS, documenting the behavior the one-line implementation must
preserve.

**Step 4: Commit the tests and plans**

```bash
git add core/tests/test_root_redirect.py docs/plans/2026-09-01-cms-55-sites-homepage-design.md docs/plans/2026-09-01-cms-55-sites-homepage.md
git commit -m "test: cover root redirect branches"
```

### Task 2: Port the pinned public homepage

**Files:**
- Create: `templates/marketing/home.html`
- Modify: `core/views.py:26`

**Step 1: Copy the pinned document into the Django template**

Preserve the inline style blocks, source variable names, section shells, KATEK
CDN assets, and responsive layout. Remove Cookiebot, the SOP assistant, and the
entire `results` section.

**Step 2: Replace copy section by section**

Use only statements approved in `MARKETPLACE-LISTING-FILL-IN.md`. Preserve the
seven-section mapping in the design document and keep `/login/` available from
the header.

**Step 3: Change the final root redirect branch**

Replace only:

```python
return redirect("login")
```

at the end of `root_redirect` with:

```python
return render(request, "marketing/home.html")
```

**Step 4: Run the five focused tests**

Run:

```bash
python manage.py test core.tests.test_root_redirect
```

Expected: five tests pass.

**Step 5: Commit the implementation**

```bash
git add core/views.py templates/marketing/home.html
git commit -m "feat: add public Katek CMS homepage"
```

### Task 3: Prove test causality

**Files:**
- Temporarily modify and restore: `core/views.py`

**Step 1: Revert the homepage render hunk**

Restore the final redirect to login temporarily and run the named anonymous
base-domain test. Expected: FAIL with a redirect instead of HTTP 200.

**Step 2: Mutate each preserved branch separately**

For each remaining named test, temporarily change only its matching branch to
the wrong response, run that single test, record the failure, and immediately
restore `core/views.py`. For the unpublished anonymous branch, temporarily
render the tenant to prove the draft-content sentinel assertion fails.

**Step 3: Verify the restored function diff**

Run `git diff origin/main -- core/views.py`. Expected: exactly one changed line,
the final anonymous no-tenant branch.

### Task 4: Verify content, rendering, and repository gates

**Files:**
- Verify: `templates/marketing/home.html`

**Step 1: Run forbidden-content greps**

Confirm there is no `randomuser.me`, results section, Cookiebot, SOP assistant,
em dash, or placeholder testimonial content in the template.

**Step 2: Run focused and full Django tests**

Run `python manage.py collectstatic --noinput`, the focused test module, and
`python manage.py test`. Expected: all tests pass.

**Step 3: Run repository lint and asset gates**

Discover the repository's configured lint commands and run all applicable
checks without adding a frontend build dependency.

**Step 4: Inspect the rendered page**

Start Django locally and capture desktop and mobile screenshots. Check first
viewport clarity, all seven sections, responsive behavior, keyboard focus,
FAQ interaction, image loading, and absence of the cut results section.

**Step 5: Run the Impeccable mechanical detector once**

Run it against the changed template and resolve applicable mechanical issues.

### Task 5: Open the PR and hand off CMS-55

**Files:**
- Verify: all committed CMS-55 files

**Step 1: Inspect commits and final diff**

Confirm no migration or model file changed and no user-owned file entered the
branch.

**Step 2: Push the feature branch**

Push over the configured personal SSH remote without bypassing hooks.

**Step 3: Open a PR against `main`**

Link CMS-55. Include the exact test and lint commands, mutation proof for every
named test, template path, one-line view hunk, external-script decision, and
the `randomuser.me` grep result.

**Step 4: Update Plane**

Comment the PR URL on CMS-55 and move the ticket to In Review. Do not merge or
mark Done.

