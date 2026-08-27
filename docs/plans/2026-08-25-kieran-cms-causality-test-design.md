# Kieran CMS causality test design

## Objective

Determine whether the current CMS import, save, edit, or render pipeline causes
the formatting and field-displacement defects visible on Kieran Haughey's live
site. Distinguish a current platform defect from an older deployment, a bad
bulk revision, or already-corrupted tenant content.

## Safety boundary

- Make no production CMS or client-content changes.
- Use only synthetic, non-client fixture copy.
- Use the isolated staging CMS and a disposable public static control.
- Record evidence before removing the staging tenant and public control.
- Preserve unrelated worktree changes.

## Approaches considered

### 1. Public control plus staging CMS A/B test — selected

Host an immutable fixture on a disposable Cloudflare Pages endpoint, import the
same URL through staging, and compare the raw, imported, saved, and edited
states. This exercises the deployed staging pipeline while retaining an exact
public control.

### 2. Local renderer-only reproduction

Fast and deterministic, but it cannot establish whether the deployed staging
or production release behaves like the current checkout. It remains a useful
supporting test, not the causal experiment.

### 3. Production throwaway tenant

Closest to Kieran's environment, but unnecessary production state and routing
risk make it inappropriate until the isolated staging experiment indicates a
deployment-specific difference.

## Fixture

Build a small static page containing the failure primitives observed on the
live site:

- headings with nested italic/accent-colour spans and significant whitespace;
- a responsive hero heading with the approved desktop/mobile size range;
- repeated statistic and source pairs with unique sentinel values;
- accordion-style repeated content with unique sentinel values;
- annotated link and rich-text fields;
- stable element IDs and `data-*` markers for automated comparison.

The fixture uses synthetic copy so no client data enters staging or the
throwaway host.

## Experiment stages

1. **Control:** capture the immutable Pages response, normalized DOM, computed
   styles, and desktop/mobile screenshots.
2. **Import:** import the control URL into a new staging template/site and
   capture the editor preview before any content edit.
3. **No-op save:** save and render without changing content. This detects
   destructive default merging or non-idempotent rendering.
4. **Targeted edit:** edit one mixed-style heading and several sequential
   rich-text fields, then save and render again. This detects field-type,
   editor serialization, mapping, and persistence failures.
5. **Local corroboration:** run the same fixture through the checkout's parser
   and renderer plus focused regression tests. Record the checkout SHA and, if
   discoverable, the staging release SHA.

## Evidence and decision rules

For each stage, collect:

- normalized element order, text, attributes, and field identifiers;
- computed font family, size, style, weight, and colour for styled nodes;
- desktop and mobile screenshots;
- stored schema/default/content values when staging access permits;
- HTTP status and rendered URLs without recording credentials.

Interpret the first divergent stage as follows:

- **Import diverges:** importer or annotator is causal.
- **No-op save diverges:** merge, persistence, sanitizer, or renderer is causal.
- **Targeted edit diverges:** editor serialization or field update logic is
  causal.
- **All staging states remain correct:** the current CMS engine is not shown to
  cause the defect; Kieran's stored record, a bulk revision, or an older
  production release becomes the supported explanation.

## Error handling

- If Cloudflare deployment is unavailable, use another disposable public
  static endpoint only if it preserves bytes unchanged.
- If staging browser authentication is unavailable, execute the same staging
  operations through an authenticated management shell or API on the isolated
  environment.
- If staging itself is unreachable, complete the local corroboration and
  report the external-access blocker without making production the fallback.
- Never print secrets, cookies, tokens, or environment values.

## Cleanup

After evidence is saved locally, delete the disposable staging tenant/template
and Cloudflare Pages project. Report what was removed and that no production
state was changed.

