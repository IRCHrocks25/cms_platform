# CMS-85 local acceptance evidence

The six screenshots use a synthetic post and a generated SVG cover, rendered by the same `blog/strips/listing.html` template used publicly. Chromium was run headless at 390 × 900 and 1280 × 900 for image left, image right, and image above. At 390px the cover stacks above text; at 1280px the left/right layouts put the cover beside text. Visual inspection found no horizontal overflow in these fixtures.

The existing Blog settings editor uses a native, visibly labeled select for each existing homepage or inner-page `data-blog-strip` marker. The default option uses the site's current `strip_style`; featured post selection and count are unchanged.

`PYTHONPATH=. /home/bernardjr/Desktop/Code/work/katalyst-ai/cms_platform/.venv/bin/python manage.py test core.tests.test_blog` passed 132/132 after the inner-page scoping addition on the feature branch under the shared build lock. The focused three tests failed before implementation and passed afterward; the broader suite found a literal index CSS selector regression, which was fixed by sharing one CSS partial with a scope argument.

After `manage.py collectstatic --noinput`, `PYTHONPATH=. /home/bernardjr/Desktop/Code/work/katalyst-ai/cms_platform/.venv/bin/python manage.py test --verbosity 1` passed 1,588 tests (six skipped) under the shared build lock. `git diff --check` passed. The initial full-suite attempt without a collected staticfiles manifest was stopped after unrelated static-manifest errors; the refreshed run is the acceptance result.

Staging/browser acceptance against Kenneth's installed account: **UNRUN**. Production: **UNRUN**. These screenshots are local synthetic fixture evidence only.
