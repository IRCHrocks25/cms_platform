# CMS-74: reusable typography tokens

## Customer report

Loom (IAM Marketable, 00:15–00:41): blog posts render in a different font and
style from the rest of the site. 01:55–02:18: no way to make text smaller.

## Root cause (reproduced from the public live article)

* The IAM template sets its fonts through CSS custom properties:
  `--disp:'Bebas Neue',sans-serif; --body:'Inter',sans-serif`, consumed by class
  rules such as `.sec-h{font-family:var(--disp)}` and
  `.btn{font-family:var(--body)}`.
* The Design tab's Body font / Heading font / Base text size controls
  (`content._global`) only write `body{...}` and `h1,...,h6{...}`. Class rules
  beat those, so headings, buttons and nav links never change.
* Theme-token discovery (`_detect_theme_tokens`) only knows colors, so the font
  variables are invisible to the editor.
* The blog (`templates/blog/_content.html`) hardcodes Roboto and Fraunces, so
  articles never use the site's fonts at all.

Reproduction in the installed client account is still owed (no access from
this environment); the analysis above uses the public published HTML.

## Token contract

Four semantic custom properties, declared in a `:root` block:

| Token | Role | Value | Design control | `_global` key |
|---|---|---|---|---|
| `--font-body` | body font | font stack | Body font | `fontFamily` |
| `--font-heading` | heading font | font stack | Heading font | `headingFamily` |
| `--text-body` | body text size | length | Base text size | `baseSize` |
| `--text-heading` | main heading size | length | Heading size | `headingSize` |

New templates should declare these names and use them everywhere the role
applies (`font-family: var(--font-heading)` on every heading-like class).

### Discovery for templates that predate the contract

Deterministic, no model involved (`core/typography.py`):

* A role is filled by its contract name first.
* **Body font**: a `:root` font-stack variable used as `font-family: var(--x)`
  in a rule whose selector list includes `html`, `body` or `:root`.
* **Heading font**: among the other font-stack variables used as
  `font-family: var(--x)`, the one whose rules match the most `h1`–`h6`
  elements in the template. Needs at least one match.
* **Body size**: a `:root` length used as `font-size: var(--x)` on `html` /
  `body`.
* **Heading size**: contract name only.
* Font stacks must be plain family names (quoted or bare) separated by commas;
  anything with `var(`, parentheses, braces or semicolons is ignored. Framework
  variables are never exposed because a variable only qualifies by playing one
  of these roles.

IAM resolves to body font = `--body`, heading font = `--disp`.

## Editing and storage

* No new content namespace. The existing Design controls keep writing
  `_global`, and a new `headingSize` key is added. The Heading size control only
  appears when the template has a heading-size token.
* The block editor's toolbar had no way to reach these controls (its Design
  button opens Brand colors only). A Fonts button opens the "Colors & fonts"
  section in the side drawer when the template has typography tokens.
* Save validation: `headingSize` is accepted only as a plain length
  (a number of up to three digits with `px`, `rem` or `em`). Font names keep
  the existing sanitizer.
* Typography tokens are removed from the Brand section's fields, so the same
  variable never has two controls. A legacy saved `brand.<name>` value still
  renders, and the Design control wins when both are set.

## Rendering

`render_site` detects the tokens and, after the template's own styles:

* For each role that has a token and a saved `_global` value, overrides the
  token: `--disp: 'Poppins', sans-serif` (generic family kept from the
  template default). The element-level `body` / `h1-h6` rule for that role is
  then skipped, because the token already reaches every intended use.
* Roles without a token keep today's `body` / `h1-h6` rules, so templates
  without typography tokens render exactly as before.
* Emits contract aliases for discovered roles whose name differs
  (`--font-body: var(--body)`), so shared surfaces can rely on the contract.
* Preview: the bridge receives the same role map (JSON in the preview page) and
  applies the same rules live.

## Blog

`templates/blog/_content.html` reads the contract with today's fonts as
fallback: `--blog-sans: var(--font-body, 'Roboto', …)` and
`--blog-serif: var(--font-heading, 'Fraunces', …)`. Blog pages are wrapped in
the site chrome through `render_site`, so tokenized sites get their own fonts
and other sites are unchanged. Blog sizes stay fixed (reading measure);
consuming `--text-*` there belongs to the client blog integration ticket.

**Visible change on rollout:** sites with discovered font tokens (IAM) will
render blog articles in the site's fonts. That is the reported fix, but it
changes published output without a client action.

## Testing

* Parser: contract names, inference on an IAM-shaped template, framework and
  unsafe values ignored, plain templates have none, Brand excludes them.
* Annotator: annotated output of a tokenized template keeps the tokens
  discoverable.
* Save: `headingSize` length validation, injection dropped.
* Renderer: overrides, element-rule skipping, alias emission, legacy templates
  byte-identical typography rules, color tokens unchanged, blog consumption.
* Browser: change body/heading family and sizes in the editor; preview,
  save/reload, desktop and mobile.
