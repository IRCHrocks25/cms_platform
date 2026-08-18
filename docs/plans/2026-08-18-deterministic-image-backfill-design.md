# Deterministic Image Backfill Design

## Finding that motivates the design

The matched restaurant benchmark differs structurally at exactly two elements: the hero and about `<img>` tags. Both are high-resolution Unsplash content photos, but both declare `alt=""`. The system prompt says every empty-alt image is decorative and must be skipped. Luna follows that instruction; `gpt-4o-mini` ignores it. The few-shot image remains in the prompt and `_strip_data_uris()` does not touch either external URL, so neither missing exemplar nor URI stripping causes the gap.

An empty alt is accessibility metadata, but imported pages do not always use it correctly. Treating it as conclusive decoration conflicts with the CMS product requirement that clients can replace visible hero and about photography. Image editability therefore needs a deterministic safety net analogous to text backfill.

## Selected design

Add `_backfill_missed_image_fields(soup)` after the existing text backfill. For each non-Brand `data-section`, it finds unannotated `<img>` elements owned by that nearest section and adds a unique `<section>.image_N` marker with `data-type="image"`.

The backfill skips images when deterministic markup makes them clearly unsuitable:

- no `src` value;
- `role="presentation"` or `aria-hidden="true"`;
- inside the same chrome ancestors excluded by text backfill;
- inside a nav/footer section or semantic `<nav>`/`<footer>` wrapper;
- numeric HTML or inline-CSS dimensions that make the image a tracking pixel or an icon at roughly 32px or smaller;
- exact class/id tokens such as `logo`, `icon`, `spacer`, `tracking`, `pixel`, or `badge`.

`alt=""` alone is not an exclusion. A non-empty alt supplies the field label; otherwise the section label/id supplies a stable fallback such as “Hero image”. Existing model annotations and nested-section ownership remain untouched. The result's existing `backfilled_fields` counter becomes the sum of text and image fields added.

## Controlled experiment

Keep the current prompt unchanged while measuring the prototype. That isolates the structural backfill: Luna should still skip empty-alt images at the model layer, and the deterministic pass should recover them. A prompt rewrite can be considered separately after the backfill behavior is accepted.

Run the same restaurant input once through `gpt-4o-mini` and Luna low/medium/high after the implementation. Compare the Phase 5 before-results with the new field, image, and backfill counts. If every model ends with the same eighteen non-Brand fields and two images, model choice no longer determines image editability for this page.

## Alternatives considered

1. **Prompt-only correction:** remove the absolute empty-alt exclusion. This reduces the immediate contradiction but remains stochastic and offers no guarantee for future models or prompts.
2. **Preprocess empty alt into descriptive text:** this could steer the model but fabricates accessibility content and still relies on model compliance.
3. **Annotate every image:** simple, but would expose tracking pixels, logos, chrome, and spacers as client fields.

The selected conservative structural pass provides a product guarantee without making every image editable.

## Verification

- Unit tests cover content images, empty alt, existing annotations, labels and IDs, nested ownership, `<picture>`, chrome, presentation roles, missing sources, and tiny images.
- An integration test proves a model-skipped image reaches the final schema and increments `backfilled_fields`.
- Four live real-path measurements use the same curated fixture.
- The full Django suite, asset reproducibility, and diff checks must pass before the unmerged branch is pushed.

