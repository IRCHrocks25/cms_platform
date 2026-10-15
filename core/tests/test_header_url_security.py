"""Header URLs are navigation data, never executable schemes."""
from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from core.renderer import render_page_from_blocks
from core.services.blocks import _safe_header_href, normalize_header, upgrade_shell_chrome_slots


UNSAFE = [
    'java\tscript:alert(1)', 'java\nscript:alert(1)',
    'java\rscript:alert(1)', 'java\x00script:alert(1)',
    '\x00JaVaScRiPt:alert(1)', 'JaVaScRiPt:alert(1)',
    'data:text/html,<script>alert(1)</script>', 'vbscript:msgbox(1)',
    'file:///etc/passwd', 'ftp://example.invalid/file', 'custom:payload',
    'java&#x09;script:alert(1)', 'jav&#97;script:alert(1)',
    'javascript&colon;alert(1)', 'javascript&#58;alert(1)',
    'javascript&#x3a;alert(1)', 'http&colon;//example.invalid',
    'https&#58;//example.invalid', '&Tab;JaVaScRiPt&colon;alert(1)',
]
SAFE = [
    '/', '#about', '/#about', '/contact/', 'contact', './contact', '../contact',
    '?q=javascript:example', '/path/javascript:example',
    'https://example.invalid/path?q=a&b=c#here', 'HTTP://example.invalid',
    'mailto:demo@example.invalid?subject=Hello%20there', 'tel:+15555550123',
    '/path%20with%20spaces', '#javascript:example',
]
SHELL = ('<html><body><header data-section="nav" data-group="Header">'
         '<span>Demo</span></header><main data-region="main"></main></body></html>')


class HeaderURLSecurityTests(SimpleTestCase):
    def test_rejects_obfuscated_and_nonallowlisted_schemes(self):
        for href in UNSAFE:
            with self.subTest(href=repr(href)):
                self.assertEqual(_safe_header_href(href), '/')

    def test_rejects_every_ascii_control_and_space_inside_script_scheme(self):
        for code in [*range(33), 127]:
            with self.subTest(code=code):
                self.assertEqual(_safe_header_href('java'+chr(code)+'script:alert(1)'), '/')

    def test_legitimate_urls_are_unchanged(self):
        for href in SAFE:
            with self.subTest(href=href):
                self.assertEqual(_safe_header_href(href), href)

    def test_menu_cta_and_legacy_region_urls_share_the_guard(self):
        for href in UNSAFE:
            with self.subTest(href=repr(href)):
                header = normalize_header({'menu': [{'href': href}], 'button': {'on': True, 'href': href}})
                legacy = normalize_header({}, regions={
                    'header-center': [{'type': 'nav-link', 'fields': {'text_href': href}}],
                    'header-right': [{'type': 'button', 'fields': {'link': href}}],
                })
                for result in [header, legacy]:
                    self.assertEqual(result['menu'][0]['href'], '/')
                    self.assertEqual(result['button']['href'], '/')

    def test_public_render_never_emits_unsafe_custom_menu_href(self):
        for href in UNSAFE:
            with self.subTest(href=repr(href)):
                content = {'regions': {'main': []}, '_header': {'menu': [
                    {'id': 'nav_abcd1234', 'label': 'Synthetic link', 'href': href, 'custom': True},
                ]}}
                html = render_page_from_blocks(upgrade_shell_chrome_slots(SHELL), content, {})
                links = BeautifulSoup(html, 'html.parser').select('[data-header-link]')
                self.assertEqual(len(links), 1)
                self.assertEqual(links[0]['href'], '/')


class HeaderURLBrowserTests(SimpleTestCase):
    def test_chromium_resolves_sanitized_public_links_as_https(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('optional playwright not installed')
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page()
                for href in UNSAFE:
                    with self.subTest(href=repr(href)):
                        content = {'regions': {'main': []}, '_header': {'menu': [
                            {'id': 'nav_abcd1234', 'label': 'Synthetic link', 'href': href, 'custom': True},
                        ]}}
                        html = render_page_from_blocks(upgrade_shell_chrome_slots(SHELL), content, {})
                        page.set_content('<base href="https://synthetic.invalid/">'+html)
                        self.assertEqual(page.locator('[data-header-link]').evaluate('(a) => a.protocol'), 'https:')
            finally:
                browser.close()
