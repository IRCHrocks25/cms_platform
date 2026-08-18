# GPT-5.6 Luna Annotation Design

## Goal

Make `gpt-5.6-luna` the default HTML annotation model, preserve compatible model overrides, expose token usage for staging verification, and finish the real annotate-to-save parity test without changing production configuration.

## Request compatibility

All annotation requests continue to use Chat Completions with JSON object response format. Every model receives `max_completion_tokens`; no model receives the unsupported `max_tokens` parameter or a non-default `temperature`.

Live API checks with the staging key established these limits:

- `gpt-5.6-luna`: accepts 65,536 completion tokens and `reasoning_effort="low"`.
- `gpt-4o-mini`: accepts 16,384 completion tokens and rejects `reasoning_effort`.
- `gpt-4o`: accepts 16,384 completion tokens.
- `gpt-4.1-mini`: reports a 32,768 completion-token cap.

The request builder will therefore use a model-aware default output budget. Luna receives 65,536 tokens plus low reasoning effort. GPT-4.1 models receive 32,768 tokens. Other models use the conservative 16,384-token fallback. An optional environment override can set the completion budget for a newly configured model.

Low reasoning effort is appropriate because section ownership and exhaustive field extraction need more than a purely mechanical pass, while higher effort would consume more of the completion budget and increase latency. The 65,536-token Luna budget leaves substantial space for both reasoning tokens and the compact annotation JSON.

## Input and chunk limits

Keep `ANNOTATE_MAX_INPUT_CHARS=500000` and `ANNOTATE_CHUNK_TARGET_CHARS=40000`.

The model sees one 40,000-character chunk per request, not the full 500,000-character page. A 40,000-character chunk is roughly 10,000 input tokens before prompt overhead. With a 65,536-token completion budget, Luna has ample room for low-effort reasoning and field JSON. The 500,000-character ceiling remains an operational guard on cost, the number of concurrent calls, and the five-minute annotation-job stale limit. Increasing either threshold would add cost and timing risk without improving structural ownership or parity.

## Usage and failure reporting

Collect prompt, completion, reasoning, and total token counts from each SDK response when available. Sum them across chunks, store them in the existing `AnnotationJob.sections` JSON payload, and return them from the status endpoint. Older mocks or providers without usage metadata continue to report zero without failing.

The existing `finish_reason == "length"` branch stays non-retryable and continues to state that the chunk was too large. This remains honest even when reasoning tokens consume part of the completion budget.

## Staging and cleanup

Set `OPENAI_API_KEY` and `OPENAI_ANNOTATE_MODEL=gpt-5.6-luna` only on `sites-staging`, then redeploy that service. Production remains untouched.

The browser audit will submit a realistic raw version of the existing restaurant sample, record overlay reconciliation and usage metadata, apply the result, and save a temporary template. It will compare saved HTML marker counts to parsed schema counts. Cleanup deletes the temporary template and the exact AnnotationJob record. The staging key remains configured as authorized.

## Git delivery

Fold local harness commit `39fb53c` and the current readiness-check changes into this branch. After tests and asset checks pass, push one PR, squash-merge it to `main`, confirm staging runs the squash commit, and then execute the live parity test.
