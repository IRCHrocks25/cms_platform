# CMS-84 form line endings

## Goal

Make browser textarea submissions save after annotation while keeping the preservation guard strict for real HTML changes and allowing templates that already contain CRs to save.

## Steps

1. Add view regression tests for LF stored HTML submitted as CRLF and CRLF stored HTML submitted as LF, plus changed content/link/attribute rejection. Run them red.
2. Normalize submitted HTML to LF at every dashboard HTML form write boundary. Normalize line endings on both sides of the rendered preservation snapshot comparison only.
3. Run focused tests and project checks, inspect the diff, commit, push a PR, and watch CI.
4. Remove local copies of the approved client template and annotation job, and report the result to PM.
