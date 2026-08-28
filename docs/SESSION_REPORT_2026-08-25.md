# Session Report — Preview Reliability + Publish Flow Polish

**Date:** 2026-08-25 → 2026-08-26
**Scope:** Block builder hardening toward the "client-ready by Thursday" goal
(`docs/WEEK_PLAN.md`).
**Result:** 2 items shipped, all tests green, no lint errors, no `collectstatic`
needed for dev.

---

## 1. Fixed "Preview didn't load" (preview reliability)

### Symptom
The live preview intermittently showed **"Preview didn't load — Your edits are
safe. Reload just the preview to try again."**, and even the "Reload preview"
button sometimes didn't recover it. It recurred after structural edits
(reorder / delete / form-select) and on the new Section → 2 Column layout.

### Root cause
Not a server crash — every `/preview/` request returned **200**; the page
rendered fine. The real bug was in how the preview reloaded:

```js
// old reloadPreview() — BUG
var src = previewFrame.getAttribute("src").split("#")[0];
previewFrame.setAttribute("src", src + "#reload-" + Date.now());
```

Changing only the `#fragment` of an iframe's `src` is a **same-document
navigation** — the iframe does **not** reload, so the preview bridge never
re-sent its `ready` message. The parent waited 12s and fell back to the error
screen. Full-page reloads (after most edits) worked only because the entire
editor reloaded; the live-only path used this broken reload.

### Fix (three layers of resilience)
1. **Real reload** — `reloadPreview()` now calls
   `previewFrame.contentWindow.location.reload()` (same-origin), with a
   cache-busting `?r=` **query** param fallback instead of a fragment.
2. **Ping / ack handshake** — the preview bridge answers an editor `ping` by
   re-sending `ready`. It's registered **first**, before any other setup, so it
   works even if later init throws. On every iframe `load`, the editor pings
   until it hears back, so a missed handshake self-heals.
3. **One automatic retry** — the watchdog does a silent real reload once before
   ever showing the "Preview didn't load" message.

### Files
- `static/js/editor.js` — `reloadPreview()`, `pingPreview()`, watchdog +
  auto-retry, iframe `load` ping loop.
- `core/renderer.py` — early `ping → ready` responder in `PREVIEW_BRIDGE_SCRIPT`.

### Trade-off
A genuinely slow render now takes up to ~24s (watchdog + one auto-retry) before
erroring, instead of 12s — the right call for the intermittent-miss case.

---

## 2. Publish flow polish (Week Plan · Wednesday · P0)

### What shipped
1. **Live / Draft status pill** in the editor top bar — a calm status chip with
   a tooltip explaining each state ("Live — visitors can see this page, and saved
   changes go public immediately" vs. "Draft — only you can see this. Publish to
   make it visible.").
2. **Plain-language publish/unpublish confirm** — clicking Publish/Unpublish
   confirms the real effect. This is where the client learns *Live = instantly
   public*; unpublish clearly warns it takes the page offline (visitors get a
   "not found").
3. **Flush-before-publish (key safety fix)** — publish is a full POST+redirect,
   so a debounced-but-unsaved edit would have been silently dropped by the
   navigation. It now flushes any pending save first; if that final save errors,
   publish is **cancelled** with a clear message rather than losing the change
   (`flushSaveThen` + publish submit handler).
4. **Render-parity test** — proves the published page matches the editor preview.

### Architecture clarification
There is **no draft-vs-published content split**. `is_published` is a pure
**visibility gate**; `public_render` serves the same canonical `content`, so a
save on a Live page is **instantly public**. A literal "unpublished changes
pending" indicator (as the plan originally imagined) would be *misleading*, so
the pill + confirm make "Live = instantly public" explicit instead. A true
draft→publish workflow would be a separate model change (out of scope).

### Files
- `templates/dashboard/editor.html` — status pill; publish form `data-*` attrs
  and clearer button tooltip.
- `static/css/editor.css` — `.editor-status-pill` (`.is-live` / `.is-draft`).
- `static/js/editor.js` — `flushSaveThen()` + `.editor-publish-form` submit
  handler (confirm + flush).
- `core/tests/test_blocks.py` — `RenderParityTests`: a mixed flat + nested block
  page renders identical instances, editable fields, and values in preview and
  public, with zero editor-only chrome leaking publicly.

---

## Verification

- `python manage.py test core.tests.test_blocks core.tests.test_client_pages core.tests.test_content_versions` → **69 passed**.
- Targeted: `RenderParityTests`, `PrimitiveRenderSweepTests`, `NestedBlockTests`,
  `test_renderer_styles` → green.
- Lint clean on all edited files.
- **No `collectstatic` needed** in dev: `static_v` serves `editor.js` /
  `editor.css` from source with an mtime cache-buster. Hard-refresh (Ctrl+F5) to
  pick up the changes.

---

## How to test manually

1. Open the editor, hard-refresh (Ctrl+F5).
2. **Preview:** add a Section → 2 Column, reorder/delete blocks — the preview
   reloads in place every time and self-heals if it ever misses.
3. **Publish:** note the **Live / Draft** pill. Make an edit, immediately click
   Publish — the confirm appears, the pending edit is saved first, then it
   publishes. Unpublish shows the "takes it offline" warning.
4. Visit the public URL and confirm it matches the editor preview.

---

## What's next (from `docs/WEEK_PLAN.md`)

- **(P0)** Code/embed block safety review — confirm the raw-HTML `code` block
  only renders on the client's published site and can't execute in the
  dashboard/editor context; document the risk.
- **(P1)** Autosave + navigation safety (broader than publish: unload warnings,
  debounce/"Saved" status audit).
- **(P1)** Empty/error-state audit (white-on-white labels, truncated values,
  dead buttons, unlabeled drawer fields).
- **(P1, deferred)** Live duplicate — assessed as a high-risk, low-ROI refactor
  (the field-init is a monolithic loop; the current reload-based duplicate
  already restores scroll + opens the new block). Plan says fall back to reload.
- **(Thursday, P0)** End-to-end client smoke test + full `manage.py test`.
