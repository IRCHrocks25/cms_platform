# Three-fix validation — 15 October 2026

**Follow-up:** the missing CMS-74 implementation is now committed and pushed
as `78e7cce9` on `feat/cms-74-typography-tokens`. It passed **130 tests in
123.109s** in its isolated branch checkout, including the newer Title and
font-group browser acceptance cases. Its raw log and branch-completion report
are committed on CMS-74. The older gap below describes `a0bd42e0`, not the
updated branch. Live-search evidence is now committed separately as `5ca2e462`.

Local verification only. Production was not edited, saved, published or deployed.
Windows PowerShell, Python 3.12.3, Django 5.1.2, Playwright 1.63.0 Chromium.
Normal project settings/password hashers; isolated Django test databases.
Temporary browser files and clean Git archives are under
`D:\codex-validation\cms-three-fixes-20261015` because C: had about 127 MB free
at the start. No existing working files were reset or stashed.

## Completed results

| Code tested | Command after `python manage.py` | Observed result |
|---|---|---|
| Shared working tree, including uncommitted changes | `test core.tests.test_typography_tokens core.tests.test_typography_groups core.tests.test_typography_browser core.tests.test_blog core.tests.test_site_search_browser core.tests.test_renderer_styles core.tests.test_save_styles --noinput --verbosity 2` | **208 tests, 168.449s, OK** |
| CMS-74 `a0bd42e050a06cbb44b3c62fad46a839c84f2c3b` clean snapshot | `test core.tests.test_typography_tokens core.tests.test_typography_browser --noinput --verbosity 2` | **23 tests, 98.481s, OK** |
| CMS-75 `bd7f3c729fc040ec504911ce0c91daafe026cc1b` clean snapshot | `collectstatic --noinput`, then `test core.tests.test_blog --noinput --verbosity 2` | **88 tests, 36.735s, OK** |
| Live search `87e22c306cef97d7c39f871f2dd06ca90d943cfa` clean snapshot | `test core.tests.test_site_search_browser --noinput --verbosity 2` | **1 test, 4.843s, OK** |

All use `C:\Users\josea\cms_platform\.venv\Scripts\python.exe`. Branch tests
run from their respective D: snapshot directories. TEMP and TMP point to the
D: validation temp directory. The shared run uses the original workspace.
Remote refs were checked using `git ls-remote` after testing and still match
the three SHAs above.

Initial CMS-75 snapshot run: 88 tests, one error due to a missing staticfiles
manifest entry for `js/blog_preview.js`. Collectstatic copied 139 static files
and post-processed 417; the complete rerun passed. No application code was
changed to suppress the error. Search's test passed with a warning that the
snapshot has no staticfiles directory; its test uses Django's static-file
test storage. The first search attempt started before extraction finished and
could not find manage.py; it is not counted as a test result.

## CMS-74 release gap confirmed

The passing shared-tree suite includes newer independent Title/font-group
changes that are **not committed on the pushed CMS-74 branch**. Its existing
23 tests passing therefore does not establish those newer requirements.

Only the newer test files were copied into the disposable CMS-74 snapshot;
implementation files stayed at `a0bd42e0`. Running
`test core.tests.test_typography_groups.TextGroupTests core.tests.test_typography_groups.RenderTests --noinput --verbosity 2`
could not load the suite: `ImportError: cannot import name 'detect_typography'
from 'core.typography'`. This is a missing implementation API, not successful
execution of the newer acceptance cases.

A direct renderer probe avoids that API dependency: render an HTML template
whose h1 consumes `--disp` and whose body consumes `--body`, with
`content._global.titleFamily = "Lora"`. On the unchanged pushed CMS-74 code:

```text
Requested independent titleFamily=Lora
Lora appears in rendered CSS: False
Title contract alias emitted: False
```

Thus the old `a0bd42e0` CMS-74 branch was **not ready for the newer
Title/font-group scope**, even though its earlier token tests passed. The user
subsequently authorized placing the changes on their respective branches.
The newer implementation was copied into an isolated CMS-74 checkout, passed
130 tests, and was committed/pushed as `78e7cce9`. No CMS-75 placement or search
implementation was included in that CMS-74 commit.

## Browser coverage and limits

- Typography: font/size controls; class/token propagation; reset; independent
  Title and slot mappings; save/reload; public desktop/mobile. Newer cases
  pass on the shared code, not the old CMS-74 snapshot.
- Blog: edit title/body, save Draft, publish, refresh; one strip inside the
  explicit mount; article preview/public content and font parity; strip
  preview/public font/padding/accent equality; index listing. Chromium widths
  1280 and 390, height 900. All 88 tests pass on the pushed CMS-75 snapshot.
- Search: typing updates results without submission, clearing restores results,
  status filters and replaced action menus work in Chromium on its own branch.

These are targeted checks, not a completed rerun of the entire Django suite.
The earlier full run was interrupted when C: filled. That full-suite gate
remains incomplete. CMS-75's production block mount has not been installed,
and required installed-account after-change/live-edit acceptance remains
pending under the user's no-production-changes instruction. Do not close
CMS-74 or CMS-75 as fully live-verified based on these local results.

Raw logs saved beside this document:

- `2026-10-15-three-fixes-shared.log`
- `2026-10-15-cms74-branch.log`
- `2026-10-15-cms75-branch.log`
- `2026-10-15-search-branch.log`
