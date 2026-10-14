# Live-search branch validation

15 October 2026, Asia/Singapore. Clean Git snapshot of commit
`87e22c306cef97d7c39f871f2dd06ca90d943cfa`, branch `codex/sites-live-search`.
Windows PowerShell; Python 3.12.3; Django 5.1.2; Playwright 1.63.0 Chromium.

Command: `C:\Users\josea\cms_platform\.venv\Scripts\python.exe manage.py test core.tests.test_site_search_browser --noinput --verbosity 2`

Result: **1 test, 4.843s, OK**. Browser covers typing, case-insensitive matches,
no matches, clearing, status filtering, input focus and replacement action menus.
Temporary files and snapshot on D:, isolated local test database.
Warning: snapshot has no staticfiles directory; the browser test uses the Django
static-file test storage. No skipped browser test.

The current shared search files and browser test are byte-identical to this
committed branch, so no duplicate application commit is needed. This commit
adds evidence only. Production was not modified or deployed. This is a targeted
browser check, not a full Django-suite pass. Raw log is saved beside this file.
