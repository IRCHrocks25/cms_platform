# Relaxed image annotation policy design

## Decision

Treat the model prompt and deterministic image backfill as two halves of one
policy. An empty `alt` attribute is not proof that an image is decorative:
imported pages commonly omit useful alternative text on hero, about, product,
gallery, and testimonial photography. The model must classify each image by its
page role and surrounding content, and the backfill remains an idempotent safety
net for content images the model misses.

Both layers exclude explicit presentation and chrome signals: `role="presentation"`,
`aria-hidden="true"`, icons, logos, spacers, tracking pixels, badges, nav/footer
images, and explicitly tiny dimensions. The model may use richer context; the
backfill deliberately uses only deterministic DOM signals.

## Alternatives considered

1. **Relax the prompt and keep the backfill (selected).** This lets the model
   produce meaningful image field names while retaining complete coverage when
   inference is stochastic.
2. **Keep the old prompt and rely on backfill alone.** Rejected because the two
   layers would encode contradictory policies and every empty-alt content image
   would require generic deterministic recovery.
3. **Rewrite empty `alt` attributes before inference.** Rejected because it
   changes source semantics and fabricates accessibility content merely to steer
   the model.

## Integrity and measurement

The backfill must skip any image already carrying `data-edit`; a regression test
will assert the existing field id remains unchanged and appears only once. The
same restaurant input will be benchmarked twice on GPT-4o mini and Luna at low,
medium, and high effort. Each run will report final coverage, image coverage,
non-content image annotations, deterministic backfill count, token usage,
latency, and cost. A separate mixed-image probe will exercise the decorative
boundary because the restaurant sample contains only two genuine content images.

Luna remains the selected model and `medium` becomes the application and staging
default. The browser audit will verify that both content photographs survive
annotation, apply, save, schema parsing, and cleanup.
