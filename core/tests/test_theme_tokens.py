"""Tests for auto-detected editable theme tokens (CSS custom properties)."""
from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from core.parser import build_schema, _detect_theme_tokens
from core.renderer import _apply_tokens, render_site

# Mirrors a Figma export: framework noise (:root --tw-*, shadcn --primary) mixed
# with a real, used color palette (--plum-deep, --green, --text).
_TPL = """<html><head>
<style>
:root{--tw-shadow:0 0 #0000;--primary:#030213;--background:#fff;--radius:.6rem;
      --plum-deep:#4e3087;--plum:#6b47b8;--green:#73c200;--text:#1e1e1e}
.btn{background:var(--plum-deep);color:#fff}
h1{color:var(--plum-deep)} h1 em{color:var(--plum)}
p{color:var(--text)} .badge{background:var(--green)}
</style>
</head><body>
<section data-section="hero"><h1 data-edit="hero.t" data-type="text">Hi</h1></section>
</body></html>"""


class DetectThemeTokensTests(SimpleTestCase):
    def _names(self):
        toks = _detect_theme_tokens(BeautifulSoup(_TPL, "lxml"))
        return {t["name"] for t in toks}

    def test_detects_used_palette_colors(self):
        names = self._names()
        self.assertIn("plum-deep", names)
        self.assertIn("plum", names)
        self.assertIn("green", names)
        self.assertIn("text", names)

    def test_excludes_framework_and_unused(self):
        names = self._names()
        self.assertNotIn("tw-shadow", names)   # framework prefix
        self.assertNotIn("primary", names)     # shadcn semantic noise-name
        self.assertNotIn("radius", names)      # not a color value
        self.assertNotIn("background", names)  # noise name + not used via var()

    def test_token_carries_label_and_value(self):
        tok = next(t for t in _detect_theme_tokens(BeautifulSoup(_TPL, "lxml"))
                   if t["name"] == "plum-deep")
        self.assertEqual(tok["value"], "#4e3087")
        self.assertEqual(tok["label"], "Plum Deep")

    def test_schema_includes_theme_tokens(self):
        schema = build_schema(_TPL)
        self.assertTrue(schema.get("theme_tokens"))

    def test_plain_template_has_no_tokens(self):
        plain = "<html><head><style>.x{color:red}</style></head><body>" \
                '<section data-section="s"><p data-edit="s.t">x</p></section></body></html>'
        self.assertEqual(build_schema(plain)["theme_tokens"], [])


class ApplyTokensTests(SimpleTestCase):
    def test_injects_root_override(self):
        soup = BeautifulSoup("<html><head></head><body></body></html>", "lxml")
        _apply_tokens(soup, {"plum-deep": "#22c55e", "green": "#000000"})
        tag = soup.find("style", attrs={"data-cms-tokens": True})
        self.assertIsNotNone(tag)
        self.assertIn("--plum-deep: #22c55e;", tag.string)
        self.assertIn(":root{", tag.string)

    def test_empty_tokens_noop(self):
        soup = BeautifulSoup("<html><head></head></html>", "lxml")
        _apply_tokens(soup, {})
        self.assertIsNone(soup.find("style", attrs={"data-cms-tokens": True}))

    def test_unsafe_name_or_value_rejected(self):
        soup = BeautifulSoup("<html><head></head></html>", "lxml")
        _apply_tokens(soup, {"x} body{display:none": "red", "ok": "blue;}evil{"})
        tag = soup.find("style", attrs={"data-cms-tokens": True})
        # bad name stripped to 'xbodydisplaynone' (harmless), bad value rejected
        self.assertIsNone(tag) if tag is None else self.assertNotIn("display:none", tag.string)

    def test_render_site_applies_tokens(self):
        content = {"hero": {"t": "Hi"}, "_tokens": {"plum-deep": "#22c55e"}}
        html = render_site(_TPL, content)
        self.assertIn("--plum-deep: #22c55e;", html)
        self.assertIn('data-cms-tokens', html)
