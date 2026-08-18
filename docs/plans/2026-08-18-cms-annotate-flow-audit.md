# CMS annotate-to-render integrity audit

Date: 2026-08-18

## Scope

The audit followed pasted or fetched HTML through background annotation,
reconciliation, status polling, compare/apply, template save, schema parsing,
the content editor, and final rendering. It also checked the two other product
surfaces that use the same annotation machinery: inline site creation and
sibling-page import.

## Findings fixed

- Field markers now belong only to their nearest section. Prefix mismatches are
  corrected, orphans are removed with reported counts, nested ownership is
  consistent, and a final marker-to-schema parity check prevents partial output.
- Duplicate field IDs are uniquified in AI output. The parser rejects duplicate
  section and field IDs so defaults cannot silently overwrite earlier values;
  manual saves report the ignored marker names after redirect.
- Non-2xx status responses, error-only bodies, invalid terminal bodies, and
  unknown states stop polling immediately and preserve the server message.
- The shared HTML editor and the separate inline-site annotator now use the same
  error, zero-section warning, retry, close, and reconciliation-count contract.
- Sibling-page imports now persist an AnnotationJob and poll it in the page list.
  A worker failure is shown to the operator instead of leaving a permanent
  "annotation is running" message.
- Save-time ignored-marker warnings cover template create/update, page
  create/update, and inline site creation. The full-screen editor now renders
  those messages after redirects.
- Large CodeMirror documents use a bounded internal scroller, and source-form
  save actions remain reachable in a sticky action row.

## Out-of-scope but real

- Existing templates keep their previously stored schema for public rendering
  until they are saved again, while the editor derives a fresh schema. A data
  migration would be needed to reconcile historical rows without changing live
  defaults unexpectedly. See `core/views.py` and `_render_preview` in
  `dashboard/views.py`.
- Sibling imports created before this change have no persisted AnnotationJob,
  so historical background failures cannot be reconstructed. New imports are
  covered.

## Verification notes

The audit is backed by focused regression tests for nearest-section ownership,
duplicate IDs, field parity, polling terminal states, both annotation UIs,
save warnings, and sibling-import job outcomes. The final release gate is the
complete Django suite plus `npm run check:assets`.
