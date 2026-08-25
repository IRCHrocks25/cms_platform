# Kieran site CMS causality investigation

**Date:** 2026-08-25  
**Live site:** <https://themenopausecoach.com/>  
**Disposable control:** Cloudflare temporary Worker (removed after capture)  
**Current code checked:** `origin/main` at `3b3964704cedc58d64dad2b5e7290d720cffb632`

## Verdict

The live site is definitively served by KATEK Site Engine/CMS, and the CMS is a
demonstrated cause of two defect classes visible on Kieran's site:

1. **Mixed-style heading flattening and lost spaces.** A heading annotated as
   `data-type="text"` loses nested design spans during an otherwise no-op render.
   The extracted default also joins text around the span without a separator.
2. **Sequentially shifted or duplicated copy after re-annotation/template
   changes.** Tenant rows store a full copy of template defaults by generated
   field id. If re-annotation reuses `p_1`, `p_2`, etc. for different DOM
   elements, the field-loss guard sees no removed ids and permits the save. The
   old stored values then overwrite the new elements in sequence.

This does **not** mean every visual mismatch is CMS-generated. Source CSS, copy,
or manual edits may explain some differences. But the two mechanisms above are
reproducible in today's `origin/main` and closely match the live failure pattern.

## Live-site evidence

The live response contains CMS annotations (`data-section`, `data-edit`,
`data-type`, `data-label`) and KATEK CDN assets. Its editable DOM contains:

- 340 unique editable field ids across 15 sections;
- 27 editable elements nested inside another editable element;
- 14 fields whose nearest `data-section` owner does not match their id prefix;
- `hero.title` on the live `<h1>` marked `data-type="text"`, rendered as
  `Navigate menopause.Naturally, powerfully.` with no accent span and no space;
- repeated/misaligned values in `facts`, `transformation`, and `method`, including
  generated `p_N` fields containing text belonging to adjacent components.

The current parser now ignores some invalid nested ownership while building a
schema, but the stored template HTML still contains those annotations and the
renderer still walks every `data-edit` element.

## Controlled experiment

### Immutable external control

A 2,854-byte fixture was deployed to a temporary Cloudflare Worker. It contained:

- two correct `richtext` headings with styled child spans;
- one deliberately legacy `text` heading with a styled child span;
- one unannotated mixed-style heading for the import backfill;
- three stat/source sentinel pairs;
- two accordion question/answer sentinel pairs.

The local fixture and downloaded Cloudflare response were byte-identical:

```text
fd0fd229fd5529b5585acb4dde17c4eaa326a65b98e149cc10a94824fd32dd68
```

This rules out Cloudflare or the source host as the mutation point.

### Exact CMS import/render/save path

The CMS fetched the Worker URL through `fetch_url_html`, created an isolated
Template and Tenant in a temporary SQLite database, rendered the merged defaults,
saved the same content as a no-op editor save, then made two targeted edits.

The experiment ran twice with identical results:

- on the investigation branch;
- on an extracted snapshot of current `origin/main` (`3b39647`).

Results:

| Check | Result |
|---|---|
| Cloudflare bytes fetched by CMS | Exact SHA-256 match |
| Correct `richtext` heading on initial render | Span preserved |
| Correct `richtext` heading after no-op save | Span preserved |
| Legacy `text` heading on initial render | Span flattened; space removed |
| Legacy `text` heading after no-op save | Still flattened |
| Stat/source order after no-op save | Unchanged |
| One targeted stat edit | Only intended stat changed |
| One targeted richtext edit | Accent span preserved |

Therefore ordinary saves do not randomly reorder correctly identified fields.
The destructive behavior depends on bad field typing or unstable/reused ids.

### Generated-id drift reproduction

Template A used:

```text
p_1 = SOURCE-ONE
p_2 = SOURCE-TWO
p_3 = SOURCE-THREE
```

The tenant stored those defaults. Template B simulated re-annotation after a new
paragraph was inserted:

```text
p_1 = NEW-INTRO
p_2 = SOURCE-ONE
p_3 = SOURCE-TWO
p_4 = SOURCE-THREE
```

The template save was allowed without `allow_field_loss`: no ids were removed.
The rendered result was:

```text
SOURCE-ONE, SOURCE-TWO, SOURCE-THREE, SOURCE-THREE
```

That is the same shift-and-duplicate shape present in Kieran's facts and later
sections.

## Root causes in current code

### 1. `text` defaults and rendering disagree

`core/parser.py::_extract_default` uses `el.get_text(strip=True)` for a text
field. For `Text <span>Accent</span>`, that becomes `TextAccent`.

`core/renderer.py::_apply_field` compares that value with `el.get_text()`, which
still contains the inter-node space. The no-op comparison fails, and
`el.string = value` replaces all child nodes with flattened text.

The `richtext` path correctly compares and preserves the inner HTML.

### 2. The annotator can still create the bad type

The current AI prompt explicitly says an `<h2>` should be `text`. The
deterministic backfill correctly chooses `richtext` for an *unannotated* heading
with child tags, but skips fields already annotated by the model. Therefore a
model-assigned mixed-style heading can still enter storage as `text`.

Kieran's live `hero.title` is exactly such a field.

### 3. Stored defaults make generated ids sticky

`create_tenant_account` initializes `Tenant.content` with the complete template
defaults, not only authored overrides. Those values then override future
template defaults forever.

The template field-loss guard compares only the old and new sets of ids. It
cannot detect that `facts.p_2` still exists but now points to a different DOM
element. A re-annotation that adds fields and repurposes generated ids therefore
passes the guard while silently remapping stored content.

## Scope and staging note

No production data or live-site settings were changed.

The authenticated staging UI was not mutated because there was no existing login
session, the local password vault was locked, and SSH correctly rejected a
changed server host key. The security check was not bypassed. Instead, the same
experiment was run against an extracted current `origin/main` snapshot with an
isolated database and the exact external control bytes. This proves current-code
behavior but does not identify the precise historical save/re-annotation event
that first corrupted Kieran's stored records; production version history is
needed for that timestamp-level attribution.

## Recommended remediation

### Kieran data repair

1. Archive the current Template versions and Tenant content before changing it.
2. Restore/re-import the approved HTML and give every mixed-style heading a
   `richtext` field.
3. Replace unstable generated `p_N` ids with semantic, stable ids and repair the
   14 ownership mismatches/nested fields.
4. Clear or explicitly remap stale stored defaults so the repaired template's
   defaults can flow through.
5. Correct the malformed CTA `href`, copy/punctuation, font colors, and spacing,
   then perform desktop/mobile visual diffs against the approved Figma design.

### Platform fixes

1. Post-process **all** annotated text fields: if the host has child tags, upgrade
   it to `richtext`, including fields already assigned by the model.
2. Make text-field default extraction and no-op comparison use the same
   whitespace semantics; never flatten a child-bearing element on a no-op.
3. Store only client-authored overrides (with explicit authorship metadata), not
   full template defaults.
4. Give generated fields stable structural fingerprints and block/reconcile a
   template save when an existing id changes semantic owner or DOM target.
5. Add regressions for span-separated headings and for insertion before
   generated `p_N` fields.

