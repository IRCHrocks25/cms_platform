# CMS Annotation Field Integrity Design

## Context

The AI annotation flow can emit `data-edit` markers that the schema parser
silently ignores. A field can point outside every section, use a prefix that
does not match its enclosing section, or sit inside a nested section that an
outer recursive scan also visits. The compare overlay displays the annotated
HTML, so an operator can see markers that will not survive schema derivation
when the HTML is saved.

The annotation poller also treats known HTTP errors as pending work, and the
source editor has no height ceiling. These failures combine poorly: the
operator can wait six minutes for an error, close the overlay, and land far
away from the inline status and save action.

## Annotation ownership and reconciliation

A field belongs to its nearest ancestor carrying `data-section`. This rule is
used by annotation reconciliation, deterministic backfill, and schema parsing.
It gives nested sections unambiguous ownership: an inner field is parsed once
by the inner section and never inherited by an outer section.

After model annotations are applied and before backfill runs, reconciliation
walks every `data-edit` element:

- If the nearest section exists and the dotted prefix differs, rewrite only
  the prefix while preserving the field suffix.
- If no section exists, remove `data-edit`, `data-type`, and `data-label`.
  Guessing a nearby section or inserting a wrapper could assign the field to
  unrelated content or alter the page layout.
- Count rewritten and dropped markers and log both values.

The final restored HTML is checked with the same `lxml` parser used by
`build_schema`. The count of non-brand schema fields must equal the count of
`data-edit` elements. Annotation fails with an actionable diagnostic if the
counts diverge, so a future parser-repair edge case cannot return dishonest
HTML.

Representative malformed exported markup was parsed with both `html.parser`
and `lxml`. The parsers repaired paragraph, list, form, and table structures
differently, but the tested fields remained inside their sections. The parser
choice will therefore remain unchanged. Final parity validation covers any
real input that does expose a relocation difference.

## Result metadata and save honesty

The annotation pipeline keeps its existing string-returning public function
for existing background imports. A structured annotation result is added for
the job worker so it can persist reconciliation, drop, and backfill counts
alongside the existing section summary. The status endpoint returns those
numbers unchanged, and the compare overlay states them explicitly.

Saving has a second integrity check. Submitted `data-edit` occurrences are
compared with the fields produced by `build_schema` using occurrence counts,
not only a set comparison. Any ignored submitted markers are reported in a
Django warning message on the destination page after the successful save.
This complements the existing published-content field-loss guard, which only
compares the old and new schemas and only blocks when published content uses a
lost field.

## Annotation failure states

The poller treats every non-2xx response as terminal and displays the server's
message immediately. A JSON body containing `error` without a non-terminal
status is also terminal. Network failures may continue retrying until the
existing deadline because they do not prove the job itself failed.

The compare overlay remains open when annotation fails. Its loading panel
becomes an explicit error state with the intact server message, Retry, and
Close controls. Apply stays disabled. A successful response with zero editable
sections becomes a warning, with copy stating that applying produces no
editable fields. The action remains available as an explicit operator choice
and is labeled accordingly.

The inline annotation status is sticky within the source editor area, so it
remains visible near the operator's working position on both template and page
HTML screens.

## Bounded editor and reachable actions

CodeMirror receives a fixed viewport-relative height using
`clamp(24rem, 60vh, 46rem)`. Its editor and scroller fill that height, and the
scroller owns vertical overflow. The hidden textarea remains the canonical
submitted value and continues receiving every CodeMirror document update.

Both forms use a shared sticky action-row class. The row remains in normal
flow, gains an opaque surface and border while sticky, and therefore does not
cover editor content when the form is short. Existing keyboard behavior,
focus styling, ARIA labeling, Tab indentation, and search keymaps remain
unchanged.

## Verification and delivery

Required tests are written and observed failing before implementation. Focused
tests cover reconciliation, parity, nested sections, poll error handling,
zero-section warning state, result metadata, and post-save warnings. The full
Django suite runs after one `collectstatic`, followed by the asset build and
`npm run check:assets`.

Editor height is measured in a real browser with a large HTML document before
and after the fix. The branch is then audited across the full paste or fetch,
annotation, polling, apply, save, schema, editor, and renderer path. In-scope
silent loss or false success is fixed; other risks are reported with file and
line evidence.

After green verification, the branch is pushed, reviewed through a pull
request, squash-merged to `main`, and verified on staging. Staging testing
records deployment identity, editor and page heights, HTTP error timing and
message, annotation and saved schema counts, and tenant editor preview health.
No production service or staging isolation protection is changed.
