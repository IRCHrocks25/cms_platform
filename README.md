# Locked CMS — Phase 1 MVP

A multi-tenant Django CMS where you paste annotated HTML and a friendly client
dashboard auto-generates from it. Clients can edit text, images, colors and
links — they cannot add or remove sections. The structure stays locked.

This is the Phase 1 scaffold from our brainstorm — enough to demo the core
idea end-to-end on one machine.

---

## What works today

- Multi-tenant data model (`Template`, `Tenant`, `MediaAsset`, `ContentVersion`)
- HTML annotation parser → schema (`core/parser.py`)
- Renderer that swaps content into HTML for both publish + live preview
- Auto-generated dashboard with **adaptive layout**:
  - 1–6 sections → single scroll
  - 7–15 → sidebar nav
  - 16+ → sidebar + search
- Field types: `text`, `richtext`, `image`, `color`, `link`, `video`,
  `ghl-embed` (GHL forms)
- **Live preview iframe** with click-to-edit
- **Click on form** → highlights element in preview
- **Click on preview** → focuses the field in the form
- Debounced autosave with status indicator
- Mobile / tablet / desktop preview toggle
- Brand tokens from `<style data-tokens>` exposed as a Brand section
- Auto rolling version history (last 10 saves)
- Publish / unpublish toggle
- Subdomain-based public rendering (`bellas.example.com` → tenant)

---

## Run it

Requires **Python 3.12** — `.python-version` pins it and the app refuses to
start on anything else (see "Running locally" in `CLAUDE.md` for why). Both
`uv` and `pyenv` read that file, so you don't have to name the version.

```bash
cd cms_platform

uv venv                                    # or: pyenv install -s 3.12 \
                                           #     && pyenv exec python -m venv .venv
. .venv/Scripts/activate          # Windows
# source .venv/bin/activate       # macOS/Linux

pip install -r requirements.txt

python manage.py makemigrations core dashboard
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Then open <http://localhost:8000/login/>.

### Register the Claude OAuth client

Set `CLAUDE_OAUTH_CLIENT_ID`, `CLAUDE_OAUTH_CLIENT_SECRET`, and the
space-separated `CLAUDE_OAUTH_REDIRECT_URIS` in the environment, then run:

```bash
python manage.py migrate
python manage.py register_claude_oauth_client
```

The command is idempotent, creates one confidential authorization-code client,
requires PKCE, and never prints the client secret. Claude.ai (and other MCP
clients) can also self-register via RFC 7591 at `/oauth/register` — advertised
as `registration_endpoint` on `/.well-known/oauth-authorization-server`.
OAuth discovery is at that well-known URL and
`/.well-known/oauth-protected-resource`; the Ninja API starts at `/api/`.

### First run, in order

1. Sign in with the superuser you just created.
2. Go to **Templates → Add template**.
3. Paste the contents of `samples/restaurant.html` into the HTML field, name
   it "Restaurant", save.
4. Go to **Sites → Create site**, pick the Restaurant template, give it a
   subdomain like `bellas`.
5. You're now in the editor. Click any text in the preview — the form jumps
   to that field. Edit the form — preview updates live. Save is automatic.
6. Click **Publish**. Visit `http://localhost:8000/site/bellas/` to see the
   public version.

---

## Annotation spec

```html
<!-- Section wrapper -->
<section data-section="hero"
         data-label="Welcome banner"
         data-icon="star"
         data-group="Home">

  <!-- Editable fields use dotted ids: <section>.<field> -->
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>

  <p  data-edit="hero.body" data-type="richtext" data-label="Body">...</p>

  <img data-edit="hero.image" data-type="image" data-label="Photo" src="...">

  <a  data-edit="hero.cta" data-type="link" data-label="CTA link" href="...">

  <span data-edit="hero.bg" data-type="color" data-label="Background"
        style="background: #fff">

  <!-- Phase 1 GHL embed slot: form is the only supported kind. -->
  <div data-edit="hero.form" data-type="ghl-embed"
       data-ghl-kind="form" data-label="Lead form"></div>

</section>

<!-- Brand tokens -->
<style data-tokens>
  :root {
    --primary: #b91c1c;     /* becomes a Brand → Primary color picker */
    --bg: #fffaf3;          /* etc. */
  }
</style>
```

Field types: `text`, `richtext`, `image`, `color`, `link`, `video`, and
`ghl-embed` (default: `text`). A GHL form slot stores either an empty string or
the self-describing value `form:<id>`; raw IDs and unknown embed kinds are
rejected while parsing or saving.

### GoHighLevel form slots

The editor loads forms asynchronously from the site's bound GHL location.
Existing installations must reconnect/re-consent after this feature ships so
their token includes the `forms.readonly` scope. OAuth tokens remain on the
server, and the forms route accepts no caller-supplied location ID.

Published pages warn when a form slot is empty and cannot have a populated
slot cleared until they are unpublished. Editor previews show the real embed
behind a submission shield and visibly state, “This is a preview, nothing is
sent.” Public empty slots render nothing.

MCP clients can use `list_embed_slots`, `list_ghl_forms`, and
`set_embed_slot`. Form writes require a current content etag and a `form:<id>`
returned for that same tenant.

---

## Where to take this next (Phase 2 ideas from our chat)

- **Section library**: build 30–50 reusable annotated sections, organized by
  industry packs (restaurant, salon, contractor)
- **Assembly UI**: check-box assembler that composes a template from sections
- **AI auto-annotator**: feed raw HTML to an LLM, get annotations back
- **Custom domains** with auto-SSL (Caddy on-demand TLS)
- **Form builder** for contact forms
- **AI assist**: "✨ improve" buttons on every text field
- **White-label / agency mode** for resellers
- **One-click rollback** from version history (data is already there)

---

## File map

```
cms_platform/
├── manage.py
├── requirements.txt
├── cms_platform/             # Django project (settings, urls, wsgi)
├── core/                     # models + parser + renderer + middleware
│   ├── models.py
│   ├── parser.py             # annotated HTML → schema
│   ├── renderer.py           # schema + content → final HTML
│   ├── middleware.py         # subdomain → tenant
│   ├── views.py              # public render endpoint
│   └── admin.py
├── dashboard/                # editor app (views + URL routes)
│   ├── views.py
│   └── urls.py
├── templates/
│   ├── base.html
│   ├── auth/login.html
│   ├── dashboard/
│   │   ├── home.html
│   │   ├── editor.html       # ★ the split-view editor
│   │   ├── tenant_form.html
│   │   ├── tenant_list.html
│   │   ├── template_form.html
│   │   ├── template_list.html
│   │   └── components/field.html
└── static/
    ├── css/base.css          # design tokens + components
    ├── css/editor.css        # editor split-view + fields
    └── js/editor.js          # form ↔ preview bridge
```
"# cms_platform" 
