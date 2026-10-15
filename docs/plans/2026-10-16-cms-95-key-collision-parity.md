# CMS-95 key collision parity check

Preview and apply now resolve internal block type keys through one function. A source section named `faq` keeps `faq` as its instance ID and rendered `data-section`/`id`, while its scoped `BlockType.key` is `faq_section` to avoid the global `faq` primitive. Source field values and `#faq` links stay attached to the original section key.

The synthetic v2 and legacy fixtures compare preview and converted render with the classic render. A mutation that changes the instance ID to `faq_section` fails the strict render comparison.

The production re-check ran inside the CMS web container in a PostgreSQL read-only transaction. Candidate functions were executed in memory; no production template, tenant, page, block, file, or deployment was changed. Only aggregates left the host:

| Classification | Homes |
| --- | ---: |
| Classic homes | 47 |
| No extractable body blocks | 30 |
| Preview mismatch, conversion refused | 3 |
| Preview equal | 14 |
| Simulated apply equal among preview-equal homes | 14 |
| Simulated apply mismatch | 0 |
| Eligible homes with a primitive key collision | 4 |
| Survey errors | 0 |

Lisa Oates is among the 14 preview-equal and simulated-apply-equal homes. The three existing preview mismatches remain refused; this patch does not broaden eligibility.
