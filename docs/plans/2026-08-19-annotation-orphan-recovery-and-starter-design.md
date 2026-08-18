# Annotation Orphan Recovery and Starter Redesign

## Goal

Make annotation resilient when the model assigns a section and its fields to sibling elements, expose that recovery to operators, and replace the starter HTML with a compact representative example that survives annotation round trips.

## Approved design

The split-root walker will keep meaningful `section` and `article` wrappers in the model-visible chunk instead of descending through them. This prevents the common single-section page shape from hiding the only valid section boundary.

After model attributes are applied and before strict reconciliation, the annotator will recover orphan fields only when they share a safe, non-document wrapper. It will promote the lowest shared wrapper to a section, remove conflicting section markers from orphan field elements, and let the existing reconciliation pass rewrite field prefixes and make field IDs unique. `html`, `body`, and the BeautifulSoup document root are never eligible. Fields without such a wrapper continue to be removed and can still produce the existing honest no-sections error.

Recovery will report two new counters: promoted sections and salvaged fields. They will travel through `AnnotationResult`, the persisted background-job summary, the status endpoint, and both annotation comparison overlays beside the existing reconciled, dropped, and backfilled counters.

The starter will use a small page skeleton with multiple meaningful sections. It will demonstrate text, rich text, image, link, and color fields, `data-group`, and editable `data-tokens` CSS variables. A deterministic mocked-model test will strip the starter annotations, run the same production annotation path used by the corpus harness, and verify that every starter field and type is recovered.

## Alternatives considered

- Loosen the parser so fields may be owned by sibling section markers. Rejected because it would make DOM ownership ambiguous and diverge from the editor/render contract.
- Promote every orphan field element into its own section. Rejected because it creates noisy schemas and disguises unrelated or malformed model output.
- Rely only on prompt changes. Rejected because the failure is model-output variability at a structural boundary and needs a deterministic safety net.

## Verification and release

The five existing Phase 10 regression tests establish the red/green boundary. The implementation will add counter propagation and starter round-trip tests, then run focused tests, the full suite, asset checks, and the complete 120-document Luna/medium corpus. Any corpus row that becomes worse blocks release. Once green, the change will be reviewed in a PR, merged to `main`, verified on staging with the exact failure sample, the starter, and a previously passing template, and then rolled to production with the authorized transient annotation smoke and no environment changes or persistent content saves.

The operator subsequently selected a targeted corpus comparison covering every
known failure shape, every previously reconciled or dropped row, representative
size bands, the starter, and a clean control. Apparent single-run regressions
were repeated three times under identical Luna/medium settings. Every delta was
within the observed default-temperature spread, so the variance gate cleared.
