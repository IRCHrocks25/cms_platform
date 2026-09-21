# Bringing the CMS Platform's "HTML → editable blocks" engine into Courseforge

**Goal:** replace/upgrade Courseforge's tenant landing-page editor (in Branding settings) with the same html-to-editable-blocks capability that Locked CMS (`cms_platform-1`) already has, since that one is materially better.

This is a planning document from a code-level review of both repos on your machine — no code was changed. It's meant to give you the real picture (they've quietly diverged into two parallel homegrown CMS engines) and a concrete path to converge them without a rewrite.

---

## 1. What each side actually is today

### Locked CMS (`cms_platform-1`) — the good one

This is a real, dedicated product, not a feature bolted onto something else. The pipeline is:

- **Paste raw HTML → AI annotator** (`core/services/annotator.py`, ~1,700 lines): sends the page to an LLM (`OPENAI_ANNOTATE_MODEL`, configurable) with a very deliberate prompt — generous-on-content/strict-on-chrome image rules, exhaustive text-capture rules (every heading, every `<li>`, stat numbers *and* their labels as separate fields, etc.), deterministic reconciliation/backfill counters so you can see what got dropped or salvaged. It tags every element with a ref, asks the model only for a compact JSON annotation plan, and applies it server-side — so it doesn't blow up on big pages.
- **Parser** (`core/parser.py`) turns the annotated HTML into a schema: sections → fields, 9 field types (`text, richtext, image, color, link, video, ghl-embed, select, embed, code`), plus a "Brand" section auto-derived from a `<style data-tokens>` block.
- **Renderer** (`core/renderer.py`, ~2,750 lines) writes content back into the HTML for live preview and publish, preserving anything unedited.
- **Block palette** (`core/services/blocks.py` + `BlockType` model, ~1,460 lines): this is the part Courseforge doesn't have at all. Clients can add/remove/reorder/duplicate agency-curated blocks inside named regions (header/footer/main) of an otherwise-locked template — a real mini page-builder, not just "edit the text that's already there."
- **Editor UI** (`dashboard/` app): adaptive layout (single scroll for small pages, sidebar nav for medium, sidebar+search for large), bidirectional click-to-edit between the form and a live iframe preview, mobile/tablet/desktop toggle, debounced autosave, rolling version history (`ContentVersion`, last 10 saves), publish/unpublish.
- **Storage:** real relational models — `Template`, `TemplateVersion`, `BlockType`, `Tenant`, `Page`, `MediaAsset`, `ContentVersion`, `AnnotationJob`.
- **Deploy:** Docker Compose, its own Postgres, its own `api/` (currently just a health endpoint — there's no public REST API for "create a tenant/site" yet, it's all session-authenticated Django views).

### Courseforge's landing editor (`course_admin_of_admin`) — the one you're unhappy with

It's a smaller, independently-built reimplementation of the *same idea*, living entirely under `myApp/cms/` + one 1,279-line template (`dashboard/cms_landing_editor.html`):

- `myApp/cms/parser.py` (115 lines), `renderer.py` (447 lines), `annotator.py` (245 lines, rule-based/manual "click an element to mark it editable"), `ai_annotator.py` (380 lines, does use AI — `gpt-4o-mini` — but only 4 field types: `text, richtext, image, link`, and a much simpler index-based prompt with no image content-vs-chrome heuristics, no exhaustive text rules).
- No block palette equivalent at all — a tenant can edit the text/images already in the template, but can't add, remove, or reorder sections. Once imported, the structure is fixed.
- No relational storage — everything (template HTML, content values, publish flag, mode) is stuffed into `TenantConfig.features['custom_pages']`, a JSON blob on a generic feature-flags field.
- The editor UI is one big hand-rolled template with inline JS (click-to-edit, RTE toolbar, undo/redo, device preview toggle) — it covers similar ground to Locked CMS's dashboard but was built from scratch a second time, without the adaptive-layout/search handling for pages with many sections.
- **Deploy:** Railway (`Procfile`, `railway.json`), separate Postgres, separate codebase entirely.

So this isn't "Courseforge has a slightly worse editor" — it's a second, less-invested build of the exact same annotate → schema → edit → render pipeline, done independently. That's exactly the kind of duplication worth collapsing.

---

## 2. The real decision: how do the two pipelines share code?

There are three honest ways to do this, and they trade off differently:

**A. Service delegation** — Courseforge stops having its own CMS logic at all. Each tenant becomes a `Tenant`/`Site` inside Locked CMS (created via a new internal API you'd have to build — today's `api/api.py` only has a health check), Courseforge iframes or reverse-proxies Locked CMS's editor and public render into the Branding tab, with an SSO/token bridge so the tenant's Courseforge login carries over. Cleanest single-source-of-truth, but it wires two separately-deployed apps (Docker Compose vs. Railway, two databases) together at runtime — an outage or slowness in one now affects the other, and you're building real auth-bridging and API surface that doesn't exist yet.

**B. Extract the engine as a shared internal package** — Pull the annotator/parser/renderer logic (and eventually the block palette) out of `core/services/*` into a standalone module with no dependency on Locked CMS's own Django models — pure functions: `raw_html -> annotated_html`, `annotated_html -> schema`, `(annotated_html, content_dict) -> rendered_html`. Locked CMS becomes a thin caller of it (proving the extraction didn't break the flagship product); Courseforge installs the same package and swaps its own weaker `myApp/cms/*` implementations for calls into it, keeping its existing JSON-blob storage untouched. One engine, two products, no runtime coupling. The cost is real refactor work on Locked CMS's side, and going forward both products depend on one package's release/versioning discipline.

**C. Port the better code directly into Courseforge, once** — Copy and adapt Locked CMS's annotator prompt, field types, and renderer logic straight into `myApp/cms/`, and rebuild the editor UI on top of Locked CMS's dashboard JS/CSS (swapping its fetch endpoints for Courseforge's own save/preview/publish URLs). Fastest to ship, no cross-repo dependency to maintain — but it's a fork: the moment you improve one, they diverge again, which is the exact problem you have today, just with better code copied over once.

**My take:** B is the right target, but it's worth doing in two steps that de-risk it — do C-as-a-bridge first (port the annotator/renderer/editor UI so Courseforge tenants get the quality win immediately), while treating the *shape* of that ported code as if it were the shared package from day one (isolated module, no Courseforge-model imports baked into the core functions). That gives you the immediate UX fix without waiting on a Locked CMS refactor, and makes a later "actually extract it as B" a relatively mechanical move instead of a second rewrite. A is worth keeping in your back pocket only if you ever want Locked CMS to become the actual hosting product behind *all* your client landing pages (not just Courseforge tenants) — that's a bigger strategic call, not a Courseforge feature decision.

---

## 3. Phased plan

**Phase 0 — Scope call.** Decide whether "parity" for v1 means content-editing parity only (richer field types, better annotation quality, better editor UX) or also block add/remove/reorder. The block palette is Locked CMS's single biggest differentiator but also its most model-coupled piece (`BlockType` + `Template.allowed_block_types` are relational). Recommend treating it as Phase 3/v2, not v1 — it's a genuine redesign, not a lift-and-adapt.

**Phase 1 — Engine swap.** Replace `myApp/cms/parser.py`, `annotator.py`, `ai_annotator.py`, and `renderer.py` with ported versions of Locked CMS's `core/parser.py`, `core/services/annotator.py`, and `core/renderer.py`, adapted to be storage-agnostic (accept/return plain HTML strings and dicts, no `Template`/`AnnotationJob` model writes). This alone gets Courseforge from 4 field types to 9, from a simple index-based AI prompt to the exhaustive content-heuristics one, and from a 447-line renderer to the more battle-tested 2,750-line one. Courseforge keeps its existing `TenantConfig.features['custom_pages']` storage — nothing about *where* things are saved has to change yet.

**Phase 2 — Editor UI swap.** Rebuild `cms_landing_editor.html` on Locked CMS dashboard's UX: adaptive layout by section count, bidirectional click-to-edit highlighting, the "Brand" tokens section, rolling version history, device preview toggle. Because Phase 1 makes the schema shape identical between the two products, this is largely lift-and-adapt of the dashboard app's JS/CSS against Courseforge's existing save/preview/publish endpoints (`dashboard_landing_cms_save/preview/publish`), not a from-scratch rebuild.

**Phase 3 (v2, optional) — Block palette.** Port the add/remove/reorder/duplicate-block concept. Given Courseforge's JSON-blob storage, this probably wants to live as a JSON structure inside `TenantConfig.features` rather than a new `BlockType` table, to avoid a schema migration — a courseforge-shaped reimplementation of the idea, not a direct port.

---

## 4. Things you'll need to decide / watch for

- **Shared package vs. fork, for real:** even doing Phase 1–2 as a "port," write the ported code as if it will become the shared package later (isolated, no ORM calls inside the core functions) — otherwise you're rebuilding the same wall in six months.
- **AI model/cost consolidation:** Locked CMS's annotator uses `OPENAI_ANNOTATE_MODEL` (currently set to something named `gpt-5.6-luna` in `.env` — worth double-checking that's the model you actually intend, it reads like a custom/internal alias rather than a public OpenAI model name); Courseforge's uses `gpt-4o-mini`. Picking one model/cost profile for all tenant landing imports is a real decision, not a detail — it changes onboarding cost per tenant.
- **Existing tenants don't get fixed retroactively.** Tenants already on `landing_mode='cms'` have HTML annotated by the *old*, weaker annotator baked into `TenantConfig.features`. Swapping the engine doesn't re-annotate their existing pages — decide whether to force a re-import for existing tenants or let them ride on old output until they re-import themselves.
- **Sanitizer policy collision.** Both sides sanitize user-supplied HTML independently (`_sanitize_uploaded_html` / `should_passthrough_landing_html` in Courseforge vs. `core/services/sanitizer.py` + `template_sanitizer.py` in Locked CMS). When you port the renderer/annotator over, audit which sanitizer policy wins — you don't want to accidentally loosen (XSS risk) or tighten (breaks existing tenant pages) what's allowed through.
- **No public API exists yet on Locked CMS** (`api/api.py` is just a health check) — if you ever do go the Option A route, that's new surface to design and secure, not something you can wire up in an afternoon.

---

## 5. What I did not do

I didn't touch either codebase, and I didn't spin up either app to click through the live editors — this is a static read of both repos' code and docs (`README.md`, `DESIGN.md` in `cms_platform-1`; `dashboard_views.py`, `myApp/cms/*`, `cms_landing_editor.html` in `course_admin_of_admin`). If you want, next step could be either (a) I sketch what the ported `myApp/cms/annotator.py` would look like concretely, or (b) I mock up the Phase 2 editor UI so you can react to it before any real porting starts.
