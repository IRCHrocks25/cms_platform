# CMS-74 isolated branch completion

15 October 2026, Asia/Singapore. Newer Title/font-group implementation copied
from the shared working folder into a separate checkout of
`feat/cms-74-typography-tokens`, based on `a0bd42e0`. Only CMS-74 implementation,
its blog typography consumers, tests, documentation and fixture were included.
CMS-75 placement/service changes, search changes and db.sqlite3 were excluded.

Windows PowerShell; Python 3.12.3; Django 5.1.2; Playwright 1.63.0 Chromium.
Normal settings/password hashers, isolated test database; TEMP/TMP on D:.

Commands, using the existing `.venv/Scripts/python.exe` runtime:

```text
python manage.py collectstatic --noinput
python manage.py test core.tests.test_typography_tokens core.tests.test_typography_groups core.tests.test_typography_browser core.tests.test_renderer_styles core.tests.test_save_styles core.tests.test_product_copy_policy core.tests.test_annotate_preservation --noinput --verbosity 2
```

Observed: **130 tests, 123.109s, OK**. No skipped browser checks. Includes
independent Title override, Title/Heading/Body slot mapping, defaults/reset,
block-contained heading discovery, saved controls, preview/public fonts and
sizes at desktop 1280px and mobile 390px, plus renderer/save regressions.

This closes the missing-local-changes gap found on the old pushed a0bd42e0
branch: the implementation now lives on the correct CMS-74 branch. The older
verification documents remain as history. This is not a completed full-suite
gate or an installed-client production rollout. Production was not modified.
Raw test output: `2026-10-15-cms74-final-branch.log`.
