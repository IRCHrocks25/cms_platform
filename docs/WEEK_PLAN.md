# Week Plan — Ship a Client-Ready Block Builder by Thursday

Goal: by **Thu Aug 27, 2026** the visual block builder is fully working and safe
for real clients to use unsupervised — add/edit/style/reorder/delete blocks,
upload media, and publish, with no dead ends or data-loss surprises.

Scope is deliberately trimmed to "make what exists solid + close the few real
gaps." New big features (e-commerce/store, section library, white-label) stay
out of scope. See `docs/BUILDER_HANDOFF.md` for the full state.

Working days: **Mon / Tue / Wed / Thu** (4 days). Each day ends with
`collectstatic` + full `manage.py test` green.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · **(P0)** must-ship ·
**(P1)** should-ship · **(P2)** nice-to-have / cut first if time runs out.

---

## Definition of "usable by clients" (the bar for Thursday)

A non-technical client, on their tenant subdomain, can:
1. See a clear starting point on an empty page and add their first block. **(done)**
2. Add any block from Quick Add, into the page or into a row column.
3. Edit text inline on the canvas; edit all fields (text, image, link, color,
   select, embed, code) in the drawer.
4. Upload an image and have it optimized automatically **(done)**, OR reuse one
   already uploaded (image library — this week).
5. Restyle an element (color, size, font, spacing, align) and see it live.
6. Reorder / duplicate / delete blocks without a jarring reload or losing place.
7. Undo a mistake. Publish. See the published page render identically.
8. Never hit a broken control, a white-on-white label, or a truncated value.

---

## Monday — Media handling done + block QA sweep

**Theme: finish media (the in-progress area) and prove every block works.**

- [x] **(P0)** In-editor **image picker/library** in the image field.
  - ALREADY BUILT: the image field has a "Choose from gallery" button
    (`data-gallery-pick`, `field.html`) that opens a full gallery modal
    (`editor.html` `#gallery-modal`) listing uploads + every image used on the
    tenant's pages, with select / use / rename / delete. Applying sets the field
    value + live preview + autosave (`applyGalleryImage` in `editor.js`) with no
    re-upload. Works on both agency and client (tenant) surfaces.
  - Verified 2026-08-24. No new work needed here.
- [~] **(P1)** Drag-and-drop an image file directly onto an image field →
  uploads via the same optimized path. (Implementing now.) Paste-to-upload
  remains a P2 follow-up.
- [x] **(P0)** **Block QA sweep** — instantiate all 32 primitives and verify each
  renders in preview AND public without error, appears on the page, keeps field
  namespacing, and leaks no preview-only chrome to the public render.
  - Done as a durable regression test:
    `core/tests/test_blocks.py::PrimitiveRenderSweepTests` (2026-08-24, all 32 green).
    This replaces manual click-through as the guard; still worth a visual pass
    in the browser during Thursday's e2e.
- [x] No broken blocks surfaced by the sweep.

**EOD gate:** image library usable; sweep results written down; tests green.

---

## Tuesday — Structural editing polish + undo you can trust

**Theme: the core edit loop feels solid and reversible.**

- [ ] **(P0)** Fix all P0 block bugs found Monday.
- [ ] **(P1)** **Live duplicate** (currently full-page reload). Make it re-sync
  like reorder/delete: clone subtree with fresh ids, insert form section +
  layer entry, rewire, reload only the preview.
  - *Risk note:* touches core editor wiring. If it isn't rock-solid by midday,
    KEEP the current reload-based duplicate (it already restores scroll +
    opens the new block) and move on. Don't ship a half-wired clone.
- [x] **(P0)** **Undo confidence pass.** FOUND + FIXED a real multi-level-undo
  bug: undo restored the newest snapshot but `restore` also pushed the current
  state as a redo point, so a second `Ctrl/Cmd+Z` bounced forward instead of
  stepping back (toggle). Added a `pop` (linear-undo) mode to
  `content_versions.restore_editable_content`: undo now consumes the snapshot and
  pushes no redo point, so repeated undo walks back A<-B<-C. Arbitrary
  history-restore keeps the redo point. Threaded `pop:true` through both restore
  views + the editor Undo button (so `Ctrl/Cmd+Z` inherits it). Snapshots are
  still taken before every save (add/edit/style/reorder/delete/duplicate) and
  before destructive restores. Regression tests:
  `test_content_versions.py::{test_pop_undo_walks_back_through_history,
  test_pop_undo_does_not_create_redo_point}` (rolling history is 25, not 10).
- [ ] **(P2)** Redo affordance (`Ctrl/Cmd+Shift+Z`) — only if undo semantics
  allow it cleanly; otherwise defer (needs version-pointer backend work).

**EOD gate:** every edit op is reversible; duplicate is either live or safely
reload-based; tests green.

---

## Wednesday — Publish flow + content safety

**Theme: what the client ships is correct, and they can't foot-gun it.**

- [x] **(P0)** **Publish flow polish.** DONE 2026-08-25.
  - Added a **Live / Draft** status pill to the editor top bar with a tooltip
    that explains each state (`editor.html` + `.editor-status-pill` in
    `editor.css`).
  - Publish/unpublish now **confirms the effect in plain language** and, crucially,
    **flushes any pending autosave before the POST+redirect** so a debounced edit
    can't be lost by the navigation. If that final save errors, publish is
    cancelled with a clear message instead of dropping the change (`flushSaveThen`
    + publish submit handler in `editor.js`).
  - Render-parity spot check shipped as a durable test:
    `test_blocks.py::RenderParityTests` — a mixed flat+nested page renders the
    same instances, editable fields, and values in preview and public, and no
    editor-only chrome leaks publicly.
  - ARCHITECTURE NOTE: there is **no draft-vs-published content split** here.
    `is_published` is a pure visibility gate; `public_render` serves the same
    canonical `content`, so a save on a Live page is instantly public. A literal
    "unpublished changes pending" indicator would be misleading, so instead the
    pill + confirm make "Live = instantly public" explicit. (If a true draft/publish
    split is ever wanted, that's a separate model change — out of scope.)
- [ ] **(P0)** **Code/embed block safety review.** The raw-HTML `code` block is
  client-editable by decision — confirm it only renders on the client's own
  published site, is never executed in the agency dashboard context, and can't
  break the editor. Document the risk in the handoff.
- [ ] **(P1)** **Autosave + navigation safety.** Warn on unload if there are
  unsaved changes; confirm autosave debounce fires and shows a "Saved" status.
- [ ] **(P1)** Empty-state and error-state audit across the editor: no
  white-on-white labels, no truncated values, no dead buttons, every drawer
  field has a label. (Continues the design-token cleanup.)

**EOD gate:** publish is trustworthy; no unsafe/broken client-facing surface;
tests green.

---

## Thursday — Full client end-to-end + hardening + handoff

**Theme: prove it end-to-end as a real client would, then lock it.**

- [ ] **(P0)** **End-to-end client smoke test** (fresh tenant, client login,
  private window). Follow `CLAUDE.md` first-run smoke test, extended for blocks:
  1. Log in as a client on `sub.localhost:8000`.
  2. Start from empty page → add hero row + columns + several block types.
  3. Inline-edit text; upload + reuse an image; set a link; change a select.
  4. Restyle elements; reorder; duplicate; delete; undo.
  5. Publish; verify the public site matches.
  6. Repeat key steps on mobile/tablet viewport toggle.
- [ ] **(P0)** Fix every bug the smoke test surfaces. This is the buffer — keep
  it clear by not overrunning earlier days.
- [ ] **(P0)** Full `collectstatic` + `manage.py test` (expect all green,
  ~1000 tests) on Python 3.12.
- [ ] **(P1)** Cross-browser check (Chrome + Firefox) of the editor + a
  published page.
- [ ] **(P0)** Update `docs/BUILDER_HANDOFF.md`: mark shipped items, list any
  known limitations that remain, and record how to run/verify.
- [ ] **(P2)** Short client-facing "how to edit your site" note (optional).

**EOD gate / DONE:** the Definition-of-usable checklist above passes end-to-end
for a real client account; suite green; handoff updated.

---

## Risk register & cut-line

If the week gets tight, cut in this order (last-cut first):
1. Redo shortcut (P2).
2. Live duplicate (P1) — fall back to the existing reload-based duplicate.
3. Drag-drop/paste image upload (P1) — the library picker + upload button is enough.
4. Client-facing how-to note (P2).

**Never cut:** block QA sweep, undo confidence, publish correctness, the
Thursday end-to-end smoke test, and a green test suite. Those are what make it
"usable by clients" versus "demoware."

---

## Daily ritual

- Start: `git status`, pull if needed, activate `.venv` (Python 3.12).
- Before running tests: `python manage.py collectstatic --noinput`.
- End: run the full suite, commit with a clear message, update this file's
  checkboxes and the handoff doc.
