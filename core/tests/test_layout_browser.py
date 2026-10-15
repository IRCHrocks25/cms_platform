"""Browser layout parity for classic-to-block conversion (CMS-95)."""
import os
import unittest

from django.test import SimpleTestCase

from core.renderer import render_page_from_blocks, render_site
from core.services import blocks

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional browser dependency
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class LayoutConversionBrowserTests(SimpleTestCase):
    def test_lisa_shaped_wrappers_and_fixed_sibling_keep_computed_style(self):
        bands = "".join(
            f'<div class="band band-{index}"><section data-section="section-{index}">'
            f'<h2>Section {index}</h2><p>Body {index}</p></section></div>'
            + ('<div class="fixed">Locked fixed element</div>' if index == 6 else '')
            for index in range(1, 13)
        )
        classic = (
            '<!doctype html><html><head><style>body{margin:0;font:16px Arial}'
            '.page{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));'
            'gap:16px;padding:20px}.band,.fixed{padding:12px;border:1px solid #333;'
            'box-sizing:border-box}.band{background:#eee}'
            '.fixed{grid-column:1/-1;background:#cdf}'
            '@media(max-width:600px){.page{grid-template-columns:1fr;gap:8px;padding:10px}}'
            '</style></head><body><main class="page">' + bands + '</main></body></html>'
        )
        shell, fragments, layout = blocks.split_shell_and_blocks_v2(classic)
        content = blocks.convert_content_to_layout_regions({}, fragments, layout)
        converted = render_page_from_blocks(
            shell, content, blocks._catalog_from_fragments(fragments),
            block_layout=layout,
        )
        self.assertEqual(len(blocks.layout_slot_ids(layout)), 12)
        self.assertEqual(len([segment for segment in layout["segments"]
                              if segment["kind"] == "fixed"]), 1)
        selector = '.page, .page > .band, .page > .fixed'
        measure = """selector => [...document.querySelectorAll(selector)].map(el => {
            const style = getComputedStyle(el), rect = el.getBoundingClientRect();
            return [el.tagName, el.className, style.display, style.gridTemplateColumns,
                    style.backgroundColor, style.padding, style.margin,
                    rect.x, rect.y, rect.width, rect.height];
        })"""
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    executable_path=os.environ.get("CHROMIUM_BIN") or None,
                )
            except Exception as exc:  # noqa: BLE001 - browser bundle is optional
                self.skipTest(f"chromium unavailable: {exc}")
            try:
                for width in (390, 1280):
                    with self.subTest(width=width):
                        results = []
                        for html in (render_site(classic, {}), converted):
                            page = browser.new_page(viewport={"width": width, "height": 900})
                            try:
                                page.set_content(html)
                                results.append(page.evaluate(measure, selector))
                            finally:
                                page.close()
                        self.assertEqual(len(results[0]), 14)
                        self.assertEqual(results[0], results[1])
            finally:
                browser.close()
