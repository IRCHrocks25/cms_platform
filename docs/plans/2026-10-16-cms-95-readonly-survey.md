# CMS-95 read-only conversion survey — 2026-10-16

The same 47 production classic tenant homes were evaluated in a read-only database transaction. The candidate converter and renderer ran in memory inside the production CMS process. No template HTML, tenant content, or client copy was exported; only counts and categorical mismatch metadata left the server. No production row, file, or installation was changed.

| Category | Existing converter | CMS-95 candidate |
| --- | ---: | ---: |
| Equal render | 6 | 14 |
| Render mismatch | 11 | 3 |
| No extractable body blocks | 30 | 30 |
| Evaluation error | 0 | 0 |

Eight of the eleven former mismatches become equal, including Lisa Oates. For all eight newly equal homes, a second DOM/CSS check found the same tag tree shape and the same ordered `<style>`/`<script>` markup after removing conversion markers (0 discrepancies). The synthetic mutation tests also show that changing a fixed element or wrapper attribute fails the strict gate.

The three remaining homes stay classic:

| Tenant ID | Structure class | First difference and reason to investigate |
| ---: | --- | --- |
| 35 | Body DOM shape | A `<br>` in the classic tree corresponds to a `<p>` in the candidate tree. The renderer changes paragraph/line-break structure; the strict gate refuses it. |
| 48 | Header attribute | An `<a>` differs by `data-header-link`. Header-link annotation/painting is not identical after assembly; the strict gate refuses it. |
| 68 | Header attribute | Same `data-header-link` difference as tenant 48; the strict gate refuses it. |

The 30 no-block homes remain outside this ticket's conversion scope. There is no installed-account conversion in this PR. In particular, Lisa's production template and content were not saved.

Anchor handling: the template sanitizer retains only exact `cms-slot:body-NNN:start/end` comments. The CMS-87 preservation snapshot keeps comments and detects removal. The application has no HTML minifier; the renderer consumes stored anchors before returning HTML, so response-side minification cannot affect slot assembly. Missing or duplicate stored anchors fail validation before template save, content save, MCP structure edits, and rendering.

The first attempt to run the survey locally was rejected by automatic approval review because it would have transferred full production HTML and content to the local machine. The survey above ran on the server and returned aggregates only.
