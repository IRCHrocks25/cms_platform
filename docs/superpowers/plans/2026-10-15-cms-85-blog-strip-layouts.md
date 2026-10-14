# CMS-85 implementation plan

1. Add failing tests for unchanged default strip, per-marker image layouts, tenant isolation, escaped post text, and blog-settings form validation.
2. Extract image-list article markup and layout CSS from the blog index into shared partials; keep the index's normalized render and layout behavior.
3. Add image-layout strip template, marker-aware injection, and per-marker selects in Blog settings. Reuse featured posts/count and global fallback.
4. Run focused tests red/green, then full Django tests and static checks under the shared build lock. Inspect 390px and 1280px browser output if available; mark staging unrun.
5. Commit, push, open PR, wait hosted CI, add CMS-85 Testing / acceptance evidence, report to PM. No merge/deploy.
