"""Tests for Google Fonts collection + injection."""
from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from core.renderer import (
    _collect_font_families,
    _inject_font_links,
    _sanitize_font_family,
)


class SanitizeFontFamilyTests(SimpleTestCase):
    def test_strips_unsafe_chars(self):
        self.assertEqual(_sanitize_font_family('Poppins"><script>'), "Poppinsscript")

    def test_keeps_spaces_and_hyphen(self):
        self.assertEqual(_sanitize_font_family("  Playfair Display "), "Playfair Display")

    def test_empty_returns_empty(self):
        self.assertEqual(_sanitize_font_family(""), "")
        self.assertEqual(_sanitize_font_family(None), "")


class CollectFontFamiliesTests(SimpleTestCase):
    def test_dedupes_across_styles_and_global(self):
        content = {
            "_styles": {
                "hero.title": {"fontFamily": "Poppins"},
                "hero.body": {"fontFamily": "Inter"},
                "a.b": {"fontFamily": "Poppins"},
            },
            "_global": {"fontFamily": "Inter", "headingFamily": "Lora"},
        }
        fams = _collect_font_families(content)
        self.assertEqual(sorted(fams), ["Inter", "Lora", "Poppins"])

    def test_no_styles_returns_empty(self):
        self.assertEqual(_collect_font_families({"hero": {"title": "x"}}), [])


class InjectFontLinksTests(SimpleTestCase):
    def test_injects_single_link_with_consent_ignore(self):
        soup = BeautifulSoup("<html><head></head><body></body></html>", "lxml")
        _inject_font_links(soup, ["Poppins", "Playfair Display"])
        links = soup.find_all("link", href=lambda h: h and "fonts.googleapis.com/css2" in h)
        self.assertEqual(len(links), 1)
        href = links[0]["href"]
        self.assertIn("family=Poppins", href)
        self.assertIn("family=Playfair+Display", href)
        self.assertIn("display=swap", href)
        self.assertEqual(links[0].get("data-cookieconsent"), "ignore")
        preconnects = soup.find_all("link", attrs={"rel": "preconnect"})
        self.assertTrue(preconnects)
        self.assertTrue(all(p.get("data-cookieconsent") == "ignore" for p in preconnects))

    def test_empty_families_injects_nothing(self):
        soup = BeautifulSoup("<html><head></head></html>", "lxml")
        _inject_font_links(soup, [])
        self.assertFalse(soup.find_all("link"))
