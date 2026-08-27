# CMS content damage: fixes, review, and the Kieran repair procedure

**Date:** 2026-08-27
**Follows:** `2026-08-25-kieran-cms-causality.md`, which proved the CMS causes the damage.
This one is what was changed, what an independent review found wrong with it, and the
procedure for repairing an affected site.

## Confirmation on the live site

Diffing the template HTML stored for `kieran-haughey` against the live response found 100
damaged fields: 63 displaced (a field holding a different field's value), 14 flattened
(a `text` field whose child markup was destroyed), 14 missing (the published snapshot is
older than the stored template), 9 other. Same stylesheet, same assets, same DOM. Only
the CMS content merge differs.

## The four defects and where they were fixed

1. **`text` fields flattening child markup.** The write path is `el.string = value`, which
   deletes child nodes, so an accent `<span>` or a `display:block` line-break span died on
   the first render. `parser.effective_field_type` now resolves a child-bearing `text`
   field as `richtext`, and both `build_schema` and `render_site` route the declared type
   through it. `annotator._upgrade_child_bearing_text_fields` fixes the attribute at
   annotation time for every field, not only the ones the model skipped.

2. **The whitespace mismatch behind it.** `_extract_default` used `get_text(strip=True)`
   (strips each text node, joins with nothing) while `_apply_field` compared against
   `get_text()` (keeps the inter-node space). The no-op check never matched, so the
   destructive branch ran on every render. Both sides now strip consistently.

3. **Stored defaults overriding the template forever.** `renderer.strip_defaults` is the
   inverse of `merge_with_defaults`, applied in `content_versions.save_tenant_content`
   (dashboard + MCP), on restore, and in the Page save path. `create_tenant_account` seeds
   `content={}`.

4. **Generated ids silently changing owner.** `templates.FieldDriftError` refuses a save
   that repoints a surviving id, unless `allow_field_drift=True`.

## What review caught

An independent review (Codex, gpt-5.6-sol) found 8 issues, 3 of them blocking. All three
were verified before being accepted.

**The stored schema is not the parsed one.** `Template.schema` is stored in the database
and public/preview rendering merges against the stored copy. Every schema written before
the parser fix holds the flattened default, so the fix reached no existing site. Measured:
`span survives w/ STALE schema: False`, `w/ FRESH schema: True`. Re-saving the template
was not a workaround: `save_template_version` returned early on byte-identical HTML
before re-deriving. Fixed by the new `rederive_template_schemas` command, and by making
that early return re-derive a stale schema.

**Equality with the current default cannot heal a displaced row.** After a renumbering the
stored value matches nothing at its own id, so the plain prune leaves it. Repair modes
were added (below). A test now asserts the plain prune's inability, so nobody assumes
otherwise.

**Drift detection by text equality was wrong in both directions.** It missed `p_1`, the
id repointed at an inserted block, whose new text matches nothing it held before, and it
flagged an ordinary reword that happened to reuse a neighbour's old words. Rewritten on
two signals: the section gained generated ids, or a shift is visible in the text
corroborated at least twice.

Also fixed: restore bypassed the sparse invariant; a candidate tripping both guards
deadlocked the form because each response forgot the previous confirmation; the
`strip_defaults` docstring overclaimed an exact inverse.

Not changed, deliberately: the "any child tag means richtext" rule stays broad. Text-typed
rendering *destroyed* those children on every render, so richtext is strictly safer in
every case, and no tag-name test separates a decorative child from a meaningful one.

## Repair procedure

Order matters. Step 1 makes the defaults correct, which is what step 2 compares against.

```bash
python manage.py rederive_template_schemas --site <subdomain>            # dry run
python manage.py rederive_template_schemas --site <subdomain> --apply

python manage.py prune_content_defaults --site <subdomain> --drop-flattened
python manage.py prune_content_defaults --site <subdomain> --drop-flattened --apply
```

Add `--across-versions` when the template has archived versions, and `--clear-generated`
only after reading the dry run; it drops every value under a positional id on the
operator's say-so, because a displaced value cannot be attributed.

`--drop-flattened` is the inferrable one: it drops a value equal to the current default
with its markup stripped the way the old parser stripped it. No client types a headline
with the space missing between two spans.

### Measured on Kieran's real data

Run against a scratch database seeded with the live template HTML, the live stored content
(340 fields), and a reconstructed pre-fix schema.

| step | result |
|---|---|
| `rederive_template_schemas --apply` | 16 field types and 16 defaults corrected |
| `prune_content_defaults --drop-flattened --clear-generated --apply` | 340 → 6 stored fields |
| render vs. the design | 100 damaged fields → 4 |

The six survivors need a human, and three are still damage:

- `method.eyebrow` / `method.title` hold each other's copy, and `transformation.eyebrow`
  holds the transformation headline. **Displacement hit semantic ids too**, not only
  generated ones, so no automated rule reaches them.
- `consultation.cta_button` is a `link` field storing the text `Book Your Free 30-Minute
  Consultation` instead of a URL. This is the malformed CTA href the 2026-08-25 report
  flagged.
- `hero.subtitle` is flattened but with a space the flattening rule would not produce, so
  it may be a real edit.
- `footer.cta_button` holds a different, valid booking URL. Genuine edit; keep it.

The live page is also published from an older template revision than the one stored (it is
missing `transformation` area 5 and four `programmes` detail bullets), so a re-push and
re-publish are needed after the repair.

## Publish gate (open decision)

`api/mcp/tools._content_still_template_defaults` refuses to publish a site whose content
still matches the template. It strips every `_`-prefixed namespace first, so a site
customized only through `_styles`, `_hidden`, or `_tokens` is called untouched. This
predates the sparse-content change.

Reviewed against comparable products: WordPress.com, Webflow, Squarespace, Wix, Ghost,
Framer, and Duda all put publish readiness in the operator's hands. None documents a
"content must differ from the template" gate. Squarespace strongly recommends replacing
demo content and says users are not licensed to publish its samples, but does not block on
it. Where those products track customization, design changes count as changes.

Recommended: count any non-empty `_styles` / `_hidden` / `_tokens` / `_global` as edited,
and keep a separate, narrower "template copy is still in place" warning rather than one
boolean. The copy check is worth keeping in some form, since restyling does not remove another
company's name or unlicensed demo images, but it should be a confirmable warning, not an
error that says the site is untouched when it has been redesigned.
