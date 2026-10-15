# Header URL allowlist implementation plan

**Goal:** Close the production header URL scheme bypass before restacking CMS-70 PR84.
**Architecture:** Preserve the shared guard and safe href output. Validate a copy with ASCII controls/space removed; allow only http/https/mailto/tel schemes or relative paths/fragments. Reject entity-encoded schemes. All menu/CTA/legacy paths retain one guard; no tenant query or permissions changes.
**Tech stack:** Python3.12, Django, BeautifulSoup, disposable SQLite.

PM approved this contract on 2026-10-15. Extending the old denylist would leave unknown protocols open; decoding and accepting encoded schemes would violate the requested contract. Use the explicit allowlist.

1. Add `core/tests/test_header_url_security.py`: controls, NUL, mixed case, entities, unknown protocols, safe URL preservation, menu/CTA/legacy normalization and public rendering. Run with `python manage.py test core.tests.test_header_url_security`; require red on main.
2. Update only `_safe_header_href` in `core/services/blocks.py`; validate normalized scheme, return original safe value. Re-run targeted suites, then revert just the guard and require failures; restore.
3. Read production Tenant/Page content in a read-only transaction; count stored header menu/CTA and legacy-region hrefs rejected by the new policy, with no values/identifiers emitted. No writes.
4. Under the shared flock run collectstatic and full SQLite suite; push personal feature branch, open standalone PR with exact evidence and UNRUN live writes/deploy. Wait exact-head CI.
5. Rebase PR84's own commit onto this security branch; run its browser/blocks/full gates and force-with-lease. Preserve authorship, report dependency and heads. No merge or deploy.
