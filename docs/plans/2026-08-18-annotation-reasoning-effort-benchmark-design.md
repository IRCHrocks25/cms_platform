# Annotation Reasoning-Effort Benchmark Design

## Objective

Measure `gpt-4o-mini` and `gpt-5.6-luna` at low, medium, and high reasoning effort through the production `annotate_html_result()` path, then prepare—but do not merge or deploy—the Luna effort selected by the operator's cost rule.

## Constraints

- Use the same de-annotated `samples/restaurant.html` input for every run. Its curated baseline is six non-Brand sections, eighteen editable fields, and two image fields.
- Run every configuration twice. Record each run rather than averaging away model variance.
- Read the API key only from the local `OPEANAI_API_KEY_STAGING` stash and expose it as `OPENAI_API_KEY` only to each benchmark process.
- Do not write staging data, update Dokploy, deploy, or merge any PR.
- Price uncached prompt tokens and completion tokens at the official per-million-token rates read on 2026-08-18. Completion usage already includes billed reasoning tokens; report reasoning separately and do not add it twice.

## Selected design

Add `OPENAI_ANNOTATE_REASONING_EFFORT` to Django settings and forward it through Compose. `_completion_request_options()` will read that setting only when the selected model is `gpt-5.6-luna`; legacy GPT-4o models will continue omitting the unsupported argument. The default initially remains `low`, matching deployed behavior, so the new configurability cannot silently alter an environment before the benchmark decides.

The benchmark will launch a fresh Django process for every run with the model and reasoning effort in its environment. That exercises the real settings load, OpenAI client construction, chunking, annotation application, reconciliation, backfill, schema validation, and usage capture. A small untracked `/tmp` runner may summarize counts and timings without printing HTML or credentials.

After calculating cost from the two official model pages, set the code default to `high` only if both high-effort Luna runs cost less than their corresponding `gpt-4o-mini` runs. Quality is assessed separately using total and image-field coverage; a cost win does not erase a coverage regression.

## Alternatives considered

1. Pass reasoning effort as a new argument through the annotator call graph. This makes benchmarking easy but creates a runtime API the application does not otherwise need.
2. Maintain a general model-capability registry. This could scale to more reasoning models, but it is unnecessary while Luna is the only supported model accepting this option.
3. Use direct hand-written API requests. This would isolate the model, but it would not measure the production prompt, parser, reconciliation, backfill, or usage path and is explicitly out of scope.

## Verification

- Unit tests prove the configured value reaches Luna requests and never reaches GPT-4o models.
- The Compose passthrough guard proves deployed containers can receive the new setting.
- Two live runs per configuration produce machine-readable metrics and cost arithmetic.
- Focused tests, the full Django suite, asset reproducibility, and a clean diff complete the branch preparation.

