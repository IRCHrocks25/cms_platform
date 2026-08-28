# Local setup, sites, and the block editor

What we did on this Windows clone (18 Aug 2026): get the app running on
Python 3.12 + SQLite, log in, open a client site, and turn the classic
locked editor into the curated **Add section** palette.

The full product loop (annotate → template → client → publish) is in
[`HOW_THE_SYSTEM_WORKS.md`](./HOW_THE_SYSTEM_WORKS.md).

---

## 1. Python 3.12 — why `python manage.py` kept failing

This repo **refuses to start** on anything but Python 3.12. The check is in
`cms_platform/python_version.py` and matches `.python-version` plus the
Docker image `python:3.12-slim`.

`python` on this machine often resolves to **3.11**:

| What you ran | Interpreter | Result |
|--------------|-------------|--------|
| `conda activate myenv` then `python` | conda `myenv` 3.11.11 | `WRONG PYTHON` |
| bare `python` in PowerShell | `Python311\python.exe` 3.11.9 | `WRONG PYTHON` |

A project venv already exists and is correct:

```powershell
.\.venv\Scripts\python.exe --version
# Python 3.12.13
```

**Always use that interpreter** (or activate the venv first):

```powershell
.\.venv\Scripts\Activate.ps1
python manage.py runserver
```

Skip activation:

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Do **not** `pip install -r requirements.txt` into conda `myenv`. That
downgrades packages other projects in that env need (Django, httpx, openai,
Pillow).

---

## 2. Database — local SQLite, not Railway Postgres

`.env` had `DATABASE_URL` pointed at
`postgres-….railway.internal`. That hostname only resolves **inside
Railway’s private network**. Locally Django died with:

```
django.db.utils.OperationalError: [Errno 11001] getaddrinfo failed
```

`DATABASE_URL` is **commented out** in `.env`. With it unset, settings fall
back to `db.sqlite3` (see `cms_platform/settings.py`).

After that we applied pending migrations:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

To talk to a remote Postgres later you need a **public** hostname (not
`*.railway.internal`), or run Postgres locally.

---

## 3. Local login

A superuser already existed on SQLite. Password was reset:

| Field | Value |
|-------|--------|
| URL | http://localhost:8000/login/ |
| Username | `admin` |
| Password | `LocalDev123!` |

This is **only** the local `db.sqlite3` account.

---

## 4. Sites on this database

Agency **Sites** list (`/dashboard/sites/`):

| Site | Subdomain | Template | Notes |
|------|-----------|----------|--------|
| Sonia 2 | `sonia-2` | Sample_website | Published |
| sonia's website | `sonias-website` | Sample_website | Draft |
| Sample_website | `sample-website` | Sample_website | Published |

All three share template pk **2**. Converting that template to a block
shell (section 6) affects all three.

### Underscore hostnames are invalid

Visiting `http://sample_website.lvh.me:8000/` raised:

```
DisallowedHost: Invalid HTTP_HOST header
The domain name provided is not valid according to RFC 1034/1035.
```

DNS labels cannot contain `_`. Django rejects the Host header **before**
the CMS runs. The tenant subdomain was renamed:

- old: `sample_website`
- new: `sample-website`

Use:

- http://sample-website.lvh.me:8000/
- or the fallback: http://localhost:8000/site/sample-website/

`lvh.me` resolves to `127.0.0.1`. In `DEBUG`, tenant routing also accepts
`*.lvh.me` and `*.localhost` even if `TENANT_BASE_DOMAIN` is still
`sites.katek.app`.

Subdomains in the new-client form already forbid underscores
(`SUBDOMAIN_RE` in `dashboard/views.py`). This row was likely created via
admin (`SlugField` allows `_`).

---

## 5. Two editors (this is not GoHighLevel)

The GHL **Quick Add** panel (Rows, 1–6 columns, Headline, Paragraph,
drag handles) is **not** this product.

This CMS has two modes on the **same** split-view editor
(`/dashboard/sites/<id>/edit/`):

| Mode | When | What the client can do |
|------|------|-------------------------|
| **Classic locked** | Template HTML has no `data-region` | Edit fields in existing sections only. No Add button. |
| **Block shell** | Template HTML has a `data-region="main"` slot + allowlisted `BlockType`s | Add / reorder / remove blocks from the **Quick Add** drawer: agency sections **and** builder primitives (rows, headline, paragraph, image, button, form), nesting blocks inside row columns. Still cannot paste raw HTML or invent CSS. |

Before conversion, Sonia 2 was classic locked — that is why **Add
section** was missing.

---

## 6. Turning Sample_website into a block shell

Command (dry-run first, then commit):

```powershell
.\.venv\Scripts\python.exe manage.py migrate_template_to_blocks 2
.\.venv\Scripts\python.exe manage.py migrate_template_to_blocks 2 --apply --force
```

`--force` was required: the three tenant homes were not byte-identical
after conversion. Original content is kept under a `_classic` key on each
row for rollback.

Result:

- Template 2 is a **shell** (chrome + `<… data-region="main">`).
- 13 `BlockType`s were created from the former body sections:
  Hero, Stats Bar, Industry Marquee, Pressure Section, Gartner Quote Band,
  The Shift, Case Study, Methodology, Engagement Tiers, About Paula,
  Testimonials, Diagnostic, Final CTA.
- All three tenant homes were rewritten to `content.regions.main`
  instances.

After a hard refresh, the left **Sections** sidebar shows **Add section**.
That opens the **Quick Add** drawer (see §6a below). Picking a card adds
that block to the chosen destination.

---

## 6a. Builder primitives (GHL-lite)

Beyond the converted agency sections, clients can compose pages from small
primitives. Seed and allowlist them onto the shell:

```powershell
.\.venv\Scripts\python.exe manage.py seed_builder_blocks --attach-all-shells
```

This creates 14 `BlockType`s — `row-1`…`row-6` (layout rows with 1–6 column
slots), Headline, Sub-headline, Paragraph, Bullet list, Rich text, Image,
Button, Form (GHL embed) — and attaches them to every block shell.

In the editor:

- **Add section** opens the Quick Add drawer with a category rail
  (Quick Add / Rows / Text / Media / Elements / Form / Sections), a search
  box, and an **Add to** dropdown to pick the destination (page bottom or a
  row column already on the page).
- Click a card to insert, or drag it onto a form section. Dropping on a row
  targets its first column; dropping on a leaf inserts into that block's
  container.
- Rows can hold other blocks (one extra nesting level; capped at
  `blocks.MAX_BLOCK_DEPTH = 2`). Nested blocks are indented in the form list.
- Everything counts toward the 40-block-per-page cap, and unknown types /
  fields are rejected server-side (`_normalize_regions`).

---

## 7. Palette labels (readability)

The first palette showed raw keys (`caseStudy`, `cta`) and faint grey
meta text. Cause: `BlockType.save()` kept `label = key` after
`get_or_create(key=…)`, and CSS used undefined `--color-ink` /
`--color-muted` tokens.

Fixes:

- `BlockType.save()` now replaces a key-shaped label with the HTML
  `data-label` (e.g. `caseStudy` → **Case Study**).
- Category falls back to `data-group` (Home / Content).
- Palette cards use `--color-text` / `--color-text-muted`, larger type,
  and hide a useless “General” stamp.
- Existing rows were re-saved so the UI shows human names.

Hard-refresh (Ctrl+F5) after pulling these static files.

---

## 8. How to open the block editor

1. Sign in as `admin` at http://localhost:8000/login/
2. **Sites** → any of the three rows → **Edit**
3. Left sidebar → **Add section**
4. Extra pages: editor **Tools → Pages** (clients may create pages only
   when the site template is a block shell)

---

## 9. Code we changed (this session)

| File | Why |
|------|-----|
| `.env` | Commented `DATABASE_URL` so local SQLite is used |
| `core/parser.py` | Block category falls back to `data-group` |
| `core/models.py` | Human `data-label` wins over a key-shaped BlockType label |
| `core/management/commands/migrate_template_to_blocks.py` | Set label/icon/category from the fragment on convert |
| `static/css/editor.css` | Readable palette cards |
| `static/js/editor.js` | Safer card text; search includes key |
| `core/tests/test_blocks.py` | Covers the key→label repair |

Local **data** (not in git unless you commit `db.sqlite3`):

- Superuser password reset
- Subdomain `sample_website` → `sample-website`
- Template 2 converted to a block shell + 13 BlockTypes

---

## 10. Related docs

- [`HOW_THE_SYSTEM_WORKS.md`](./HOW_THE_SYSTEM_WORKS.md) — operator loop, pages, blog, publish
- [`../CLAUDE.md`](../CLAUDE.md) — architecture, dashboards, annotation DSL
- Convert command help: `python manage.py migrate_template_to_blocks --help`
