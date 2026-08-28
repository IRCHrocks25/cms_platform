# Builder Handoff — Visual Block Builder

Working notes so you can restart your PC and pick up exactly where we left off.
Everything below is about the **client block builder** (the GHL-lite visual editor)
and the UI-chrome redesign that sits on top of it.

Last updated: 2026-08-27

Bug-review pass (see `docs/FULL_SYSTEM_BUG_REVIEW.md` status): A1–A17, C1–C4, C6, E1–E14, X1 are fixed. **C5 is an accepted residual** — Live writes the public content plane immediately; there is no draft snapshot.

---

## 1. How to get running again after a restart

```powershell
# from the repo root: e:\New Downloads\New_clone_projects\cms_platform-2
.venv\Scripts\activate

# if the venv is broken / conda is active, call python directly instead:
#   .venv\Scripts\python.exe manage.py <command>

python manage.py migrate
python manage.py collectstatic --noinput   # REQUIRED — hashed static storage
python manage.py runserver
```

Then open `http://localhost:8000/login/`.

- **Python must be 3.12** (pinned in `.python-version`). If you see a `WRONG PYTHON`
  banner, deactivate conda and use `.venv`, or run `.venv\Scripts\python.exe` directly.
- **Database is local SQLite.** `DATABASE_URL` is commented out in `.env` on purpose
  (the remote Postgres host isn't reachable from here). Leave it commented for local dev.
- **`collectstatic` is not optional.** Settings use hashed-manifest static storage, so
  any change to `static/css/editor.css` or `static/js/editor.js` only shows up after
  you re-run `collectstatic`. If tests error with "Missing staticfiles manifest entry",
  that's the cause.

### Login / credentials

- Superuser username: `admin` (agency operator dashboard at `app` host / bare localhost).
- If you forgot the password, reset it instead of guessing:

```powershell
.venv\Scripts\python.exe manage.py changepassword admin
```

- The client editor lives on a tenant subdomain, e.g. `http://sample-website.localhost:8000/`.
  Subdomains with underscores are invalid — use hyphens (`sample-website`, not `sample_website`).

### Seeding / migrating blocks

```powershell
# create/refresh the primitive block library (rows, text, media, 30+ blocks)
.venv\Scripts\python.exe manage.py seed_builder_blocks --attach-all-shells

# convert a classic locked template into a block-shell page
.venv\Scripts\python.exe manage.py migrate_template_to_blocks --apply
#   add --force if it warns pages render differently
```

---

## 2. What the builder is

A direct-manipulation visual page builder for **clients** (not just the agency),
modelled on GoHighLevel / Framer / Webflow / Figma. Clients add blocks from a
**Quick Add** drawer, drag to reorder, edit text inline on the canvas, and tune
per-element styles in a right-hand slide-over drawer. Structure is still
client-safe — they compose from a curated primitive library rather than writing
layout from scratch.

Two preview surfaces exist; don't conflate them (see `CLAUDE.md`): the editor
preview is server-rendered with a postMessage bridge; the template-author preview
is raw `srcdoc`.

---

## 3. What's been built (done)

### Block system / backend
- **Nested blocks**: rows → columns → leaf blocks, capped by `MAX_BLOCK_DEPTH`
  and `MAX_BLOCKS_PER_PAGE`. Server-side `_normalize_regions` validates/sanitizes
  the nested tree recursively.
- **Primitive block library (32 blocks)** seeded by
  `core/management/commands/seed_builder_blocks.py`: rows/columns, headline,
  sub-headline, paragraph, rich text, bullet list, button, GHL form, divider,
  spacer, icon, video, image, image slider, photo gallery, logo showcase, FAQ,
  reviews, number counter, pricing table, progress bar, image feature, social
  icons, map, QR code, **raw HTML code block**, countdown/timers, testimonials.
- **New field types** (`core/parser.py`, `core/renderer.py`,
  `templates/dashboard/components/field.html`):
  - `select` — configurable options via `data-options` (e.g. testimonial column
    count / look variants); reusable across blocks.
  - `embed` — a plain URL the client types that fills an element's `src`.
  - `code` — raw HTML the client controls (rule intentionally relaxed to allow clients).
- **New style controls**: line-height, letter-spacing, text-transform added to the
  per-element style panel and wired end-to-end (UI → JS → preview bridge → render/save).

### UI chrome redesign (design-token system)
- Three-layer tokens (primitive → semantic → component) added as
  `BUILDER CHROME TOKENS` (`--ui-*`) in `static/css/editor.css`, layered over the
  `dashboard.css` primitives. Scoped to the builder page only.
- **Inter** for chrome, **Roboto** kept for brand/content. `tabular-nums` on numeric inputs.
- Refactored surface-by-surface: top bar → property drawer → left panel → canvas,
  each with full interaction states (rest / hover / focus-visible / active / selected /
  disabled / drag).

### Stage A — panel ↔ canvas linking (done)
- Click an element on the canvas → selects it, opens the right settings drawer, and
  scrolls/highlights the matching field(s) in the content panel.
- Reverse: hover/focus a field → outlines + scrolls to the element on the canvas.
- On-canvas **selection frame** with block-name label + mini-toolbar
  (move up/down, duplicate, delete, drag grip).
- Drawer offsets the canvas so it never covers the element being edited.

### Stage B — navigable content panel (done)
- Repeated fields grouped into collapsible **Card 1 / Card 2 …** groups (collapsed by
  default), one consolidated "Style" disclosure per group.
- Left rail is a real **layers/outline tree**: deduped, page sections separated from
  layout blocks, depth-indented, meaningful icons, filterable via the search box.
- Auto-growing textareas so long values aren't truncated.

### Stage C — drag (done)
- Drag handles to reorder sections, blocks, and columns.
- Palette → canvas drag with a live 2px accent insertion line and valid/invalid drop
  targets; drag ghost ~70% opacity, snappy motion.
- Click-to-"Add block" kept as fallback.

### Live structure ops (no jarring reload) (done)
- Reorder and **delete** re-sync the DOM live (form panel + sidebar tree + controls)
  and reload only the preview iframe, preserving scroll position and selection.
  This fixed the "it reloaded and jumped to the top" complaint.
- (Live **duplicate** was deliberately deferred — see Next.)

### Stage D — inline editing on canvas (done)
- Double-click text/richtext on the canvas → edit in place (`contentEditable`),
  commit on blur/Enter, cancel on Esc, writes back to the field schema + form field.
- Floating text mini-toolbar with bold / italic / **link** (prompts for URL).
- Drawer text editors collapsed under a subtle "Text content" disclosure + a hint
  banner pointing users to on-canvas editing (drawer is now properties-focused).

### Keyboard + empty state (done)
- Shortcuts for a selected block: `Delete`/`Backspace` remove, `Ctrl/Cmd+D` duplicate,
  arrow keys navigate, `Esc` deselect/close drawer.
- **Global `Ctrl/Cmd+Z`** undo (works on classic pages too).
- Floating **"?"** button + popover listing all shortcuts (press `?` to toggle).
- **Empty-canvas "getting started" state** on block-shell pages: "Start building your
  page", an "Add your first block" CTA that opens Quick Add, and inline tips
  (double-click to edit, drag to move, `?` for shortcuts).

---

## 4. Key files (where things live)

| Area | File |
|------|------|
| Block schema parsing / field types | `core/parser.py` |
| Block/page rendering + preview bridge JS | `core/renderer.py` |
| Nested content normalize + editor context | `dashboard/views.py` (`_normalize_regions`, `_render_editor`, `_normalize_styles`) |
| Block catalog / seeding / regions | `core/services/blocks.py` |
| Primitive block HTML library | `core/management/commands/seed_builder_blocks.py` |
| Classic → block conversion | `core/management/commands/migrate_template_to_blocks.py` |
| Editor markup (panels, drawer, Quick Add, zero-state) | `templates/dashboard/editor.html` |
| Per-field rendering (incl. select/embed/code, style panel) | `templates/dashboard/components/field.html` |
| All editor JS (DnD, drawer, inline edit, shortcuts, live re-sync) | `static/js/editor.js` |
| Builder chrome tokens + component styles | `static/css/editor.css` |
| Models (`Template`, `BlockType`, schema derivation) | `core/models.py` |

`★` files most likely to touch next: `renderer.py`, `editor.js`, `editor.css`,
`seed_builder_blocks.py`.

---

## 5. Next / missing plans

Roughly in priority order. None are started unless noted.

1. **Live duplicate** (deferred from live ops). Duplicate currently still does a full
   `saveAndReload`. Make it re-sync like reorder/delete: clone the instance + its
   descendants in the tree, insert new form sections + sidebar entries, refresh
   controls, reload only the preview. Higher risk because new instance IDs must be
   minted client-side and stay consistent with the server on next save.

2. **Media / image handling** (in progress).
   - Uploads DO go to a CDN via `core/services/iceberg_media.py` (Iceberg:
     init-upload → PUT → complete), validated at the door with Pillow
     (`validate_image`). A media list/rename/delete API already exists in
     `dashboard/views.py` (~line 3320+).
   - DONE: **server-side image optimization** — `optimize_image_upload()` in
     `iceberg_media.py` downscales the longest edge to
     `MEDIA_IMAGE_MAX_DIMENSION` (default 2000px) and recompresses in-format
     (JPEG q82 / PNG / WebP), baking in EXIF orientation and dropping metadata.
     Animated/GIF/unknown/undecodable uploads fall back to the original untouched.
     Tuning: `MEDIA_IMAGE_MAX_DIMENSION`, `MEDIA_IMAGE_JPEG_QUALITY`. Tests in
     `core/tests/test_iceberg_media.py::OptimizeImageTests`.
   - DONE: an in-editor **image picker/library** already exists — the image
     field's "Choose from gallery" button (`data-gallery-pick`) opens
     `#gallery-modal`, which lists uploads + every image used on the tenant's
     pages (select / use / rename / delete). Applying sets the value + live
     preview + autosave (`applyGalleryImage`). Works for agency + client.
   - DONE: **drag-and-drop image upload** onto an image field (reuses the
     optimized upload path). See `editor.js` `ftype === "image"`.
   - TODO (next): paste-to-upload (P2); drag-drop onto a whole block/canvas.

3. **Version history UI.** `ContentVersion` data exists (rolling 10) but there's no UI
   to browse or restore. Build a "History" panel that lists versions and restores one.

4. **Publish flow polish.** Confirm the publish button state/feedback, unpublished-change
   indicator, and public render parity for the new block types.

5. **Redo + multi-step undo.** Undo is wired but confirm depth and add redo
   (`Ctrl/Cmd+Shift+Z`).

6. **E-commerce / Store blocks** (backlogged by decision). Cart, product list, order
   steps, upsell need a real shopping backend — out of scope until that exists.

7. **Section-library / white-label** ideas from the original brainstorm — still Phase 2+,
   don't start without discussion.

---

## 6. Gotchas to remember

- Re-run `collectstatic` after ANY `editor.css` / `editor.js` change or you'll debug a ghost.
- Run the full suite with `python manage.py test` after `collectstatic`
  (expected ~821 tests OK; a broken baseline usually means you skipped collectstatic
  or you're on the wrong Python).
- `data-cms-label` and other preview-only attributes must only be emitted when
  `preview=True` — public renders must stay byte-for-byte identical (render-parity tests).
- Don't let `Template.schema` drift; `Template.save()` rebuilds it from HTML.
- `Tenant.content` is canonical; always go through `merge_with_defaults()`.
- postMessage bridge strings (`cms-editor` / `cms-preview`) must match on both sides.
