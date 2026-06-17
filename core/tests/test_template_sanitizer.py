"""Tests for the permissive template-aware sanitizer used by the renderer
when applying tenant richtext edits.

The pre-existing ``core/services/sanitizer.py`` is built for untrusted
blog body input (tight allowlist, strips classes / styles / data-attrs,
unwraps everything outside its small tag list). Applying it on every
render of an agency-annotated template visibly destroys the design —
utility classes vanish, structural wrappers unwrap, design tokens drop.

``sanitize_template_html`` is the wider-allowlist replacement used only
on the renderer path. It has to:

1. Preserve every ``class`` / ``style`` / ``id`` / ``data-*`` / ``aria-*``
   the design needs.
2. Allow the structural and inline tags the agency uses
   (``div``, ``span``, ``section``, ``h1`` … ``h6``, etc.).
3. Still close the small XSS surface a malicious tenant could try to
   inject: ``<script>``, event handlers, ``javascript:`` URLs, CSS-based
   XSS patterns.

``canonicalize_fragment`` exists so the renderer's no-op short-circuit
survives cosmetic round-trip drift from BS4's parsers.
"""
from bs4 import BeautifulSoup
from django.test import TestCase

from core.parser import build_schema
from core.renderer import merge_with_defaults, render_site
from core.services.template_sanitizer import (
    canonicalize_fragment,
    sanitize_template_html,
)


class TemplateSanitizerPreservesDesignTests(TestCase):
    def test_class_attribute_preserved_on_span(self):
        out = sanitize_template_html(
            "<span class='accent gradient-text'>Hi</span>"
        )
        self.assertIn("<span", out)
        self.assertIn("class=\"accent gradient-text\"", out)

    def test_class_attribute_preserved_on_div(self):
        out = sanitize_template_html(
            "<div class='hero-grid col-2 gap-8'>Body</div>"
        )
        self.assertIn("<div", out, "<div> must not be unwrapped")
        self.assertIn("hero-grid", out)
        self.assertIn("col-2", out)
        self.assertIn("gap-8", out)

    def test_structural_tags_survive(self):
        for tag in ("div", "section", "article", "header", "footer", "nav",
                    "aside", "main", "h1", "h5", "h6", "span"):
            out = sanitize_template_html(f"<{tag} class='x'>inside</{tag}>")
            self.assertIn(f"<{tag}", out, f"<{tag}> must not be unwrapped")
            self.assertIn("class=\"x\"", out, f"class on <{tag}> must survive")

    def test_inline_style_preserved(self):
        out = sanitize_template_html(
            "<p style='background: #abc; padding: 12px;'>Body</p>"
        )
        self.assertIn("style=", out)
        self.assertIn("background", out)

    def test_data_attributes_preserved(self):
        out = sanitize_template_html(
            "<div data-analytics='cta-hero' data-section='hero'>Body</div>"
        )
        self.assertIn("data-analytics=\"cta-hero\"", out)
        self.assertIn("data-section=\"hero\"", out)

    def test_aria_and_role_preserved(self):
        out = sanitize_template_html(
            "<nav aria-label='Primary' role='navigation'>Links</nav>"
        )
        self.assertIn("aria-label=\"Primary\"", out)
        self.assertIn("role=\"navigation\"", out)

    def test_id_attribute_preserved(self):
        out = sanitize_template_html("<section id='pricing'>Body</section>")
        self.assertIn("id=\"pricing\"", out)

    def test_nested_structure_with_classes_survives_intact(self):
        out = sanitize_template_html(
            "<div class='card'>"
            "<header class='card-h'><h3 class='title'>T</h3></header>"
            "<p class='lede'>L</p>"
            "</div>"
        )
        for needle in ("card", "card-h", "title", "lede",
                       "<div", "<header", "<h3", "<p"):
            self.assertIn(needle, out)


class TemplateSanitizerBlocksXssTests(TestCase):
    def test_script_tag_removed_entirely(self):
        out = sanitize_template_html(
            "<p>Hi</p><script>alert(1)</script><p>Bye</p>"
        )
        self.assertNotIn("<script", out)
        self.assertNotIn("alert", out)
        # Surrounding paragraphs must survive.
        self.assertIn(">Hi<", out)
        self.assertIn(">Bye<", out)

    def test_iframe_removed(self):
        out = sanitize_template_html("<iframe src='evil.com'></iframe><p>OK</p>")
        self.assertNotIn("<iframe", out)
        self.assertIn(">OK<", out)

    def test_onclick_attribute_dropped(self):
        out = sanitize_template_html(
            "<button onclick='hack()' class='btn'>Click</button>"
        )
        self.assertNotIn("onclick", out)

    def test_onerror_attribute_dropped_on_img(self):
        out = sanitize_template_html(
            "<img src='/a.png' alt='a' onerror='hack()'>"
        )
        self.assertNotIn("onerror", out)
        self.assertIn("src=\"/a.png\"", out)

    def test_javascript_url_in_href_dropped(self):
        out = sanitize_template_html("<a href='javascript:hack()'>x</a>")
        self.assertNotIn("javascript:", out.lower())

    def test_javascript_url_in_img_src_dropped(self):
        out = sanitize_template_html(
            "<img src='javascript:hack()' alt='x'>"
        )
        self.assertNotIn("javascript:", out.lower())

    def test_style_with_javascript_dropped(self):
        out = sanitize_template_html(
            "<div style='background:url(javascript:hack())'>x</div>"
        )
        self.assertNotIn("javascript:", out.lower())
        # <div> + its inner text must still survive.
        self.assertIn("<div", out)
        self.assertIn(">x<", out)

    def test_style_with_expression_dropped(self):
        out = sanitize_template_html(
            "<div style='width:expression(alert(1))'>x</div>"
        )
        self.assertNotIn("expression", out.lower())

    def test_data_attribute_with_handler_name_preserved(self):
        # data-* must not be incorrectly conflated with event handlers.
        out = sanitize_template_html(
            "<div data-onclick='not-a-handler'>x</div>"
        )
        self.assertIn("data-onclick", out)

    def test_form_input_removed(self):
        out = sanitize_template_html("<form><input name='x'></form><p>K</p>")
        self.assertNotIn("<form", out)
        self.assertNotIn("<input", out)
        self.assertIn(">K<", out)

    def test_srcset_with_javascript_entry_drops_only_bad_entry(self):
        out = sanitize_template_html(
            "<img src='/a.png' alt='a' "
            "srcset='/a.png 1x, javascript:hack() 2x'>"
        )
        self.assertNotIn("javascript:", out.lower())
        # Good entry survives.
        self.assertIn("/a.png 1x", out)


class CanonicalizeFragmentTests(TestCase):
    def test_attribute_order_normalized(self):
        a = canonicalize_fragment("<span class='x' id='y'>Hi</span>")
        b = canonicalize_fragment("<span id='y' class='x'>Hi</span>")
        self.assertEqual(a, b)

    def test_entity_normalized(self):
        a = canonicalize_fragment("Hi &amp; bye")
        b = canonicalize_fragment("Hi & bye")
        self.assertEqual(a, b)

    def test_empty_input_returns_empty(self):
        self.assertEqual(canonicalize_fragment(""), "")
        self.assertEqual(canonicalize_fragment("   "), "")
        self.assertEqual(canonicalize_fragment(None), "")  # type: ignore[arg-type]

    def test_identical_inputs_match(self):
        s = "<p class='lede'>Hello <em>world</em></p>"
        self.assertEqual(canonicalize_fragment(s), canonicalize_fragment(s))


class RendererUsesTemplateSanitizerOnRealEditsTests(TestCase):
    """When the tenant DOES make a real edit, the renderer used to send the
    new value through the blog-body sanitizer and strip the agency's classes
    out of the surrounding structure. With the template sanitizer wired in,
    the edit lands but the design survives.
    """

    def test_real_text_edit_to_styled_richtext_keeps_inline_class(self):
        template = (
            "<section data-section='hero'>"
            "<h2 data-edit='hero.title' data-type='richtext'>"
            "Hello <span class='accent'>world</span>"
            "</h2></section>"
        )
        # Tenant edits the inner text, keeping the same span+class structure
        # (this is the common case once contenteditable is plumbed properly).
        out = render_site(template, {
            "hero": {"title": "Welcome <span class='accent'>back</span>"},
        })
        self.assertIn("class=\"accent\"", out,
                      "class on inline <span> must survive a real edit")
        self.assertIn(">back<", out, "edit content must be applied")
        self.assertNotIn(">world<", out, "old content must be replaced")

    def test_real_edit_to_richtext_with_inner_div_keeps_wrapper_class(self):
        template = (
            "<section data-section='hero'>"
            "<div data-edit='hero.body' data-type='richtext'>"
            "<div class='wrapper'><h1>Title</h1></div>"
            "</div></section>"
        )
        out = render_site(template, {
            "hero": {
                "body": "<div class='wrapper'><h1>New title</h1></div>",
            },
        })
        self.assertIn("class=\"wrapper\"", out)
        self.assertIn("<h1>", out, "<h1> must NOT be unwrapped on real edits")
        self.assertIn(">New title<", out)

    def test_real_edit_strips_script_tag_from_user_input(self):
        template = (
            "<section data-section='hero'>"
            "<p data-edit='hero.body' data-type='richtext'>Original</p>"
            "</section>"
        )
        out = render_site(template, {
            "hero": {"body": "Safe<script>alert(1)</script>"},
        })
        self.assertNotIn("<script", out)
        self.assertNotIn("alert", out)
        self.assertIn(">Safe<", out)

    def test_real_edit_strips_onclick_handler_from_user_input(self):
        template = (
            "<section data-section='hero'>"
            "<p data-edit='hero.body' data-type='richtext'>x</p>"
            "</section>"
        )
        out = render_site(template, {
            "hero": {"body": "<span onclick='hack()'>Click</span>"},
        })
        self.assertNotIn("onclick", out)
        self.assertIn(">Click<", out)


class RendererCanonicalShortCircuitTests(TestCase):
    """Even when BS4's first-extract and second-parse produce slightly
    different strings for the same fragment, the renderer must recognize
    the value as unchanged and skip the destructive re-injection path.
    """

    def test_unedited_richtext_with_entity_drift_keeps_classes(self):
        # Build a value whose default extraction may differ from the
        # source byte-for-byte (entities reencoded).
        template = (
            "<section data-section='hero'>"
            "<p data-edit='hero.body' data-type='richtext'>"
            "Hi &amp; bye <span class='accent'>!</span>"
            "</p></section>"
        )
        schema = build_schema(template)
        content = merge_with_defaults(schema, {})
        out = render_site(template, content)
        self.assertIn("class=\"accent\"", out,
                      "class must survive unedited render even if "
                      "default and source disagree byte-for-byte")
        self.assertIn("<span", out, "<span> must NOT be unwrapped")

    def test_unedited_richtext_with_attribute_order_drift_keeps_id(self):
        template = (
            "<section data-section='hero'>"
            "<div data-edit='hero.body' data-type='richtext'>"
            "<span id='a' class='b' data-x='c'>n</span>"
            "</div></section>"
        )
        schema = build_schema(template)
        content = merge_with_defaults(schema, {})
        out = render_site(template, content)
        for attr in ("id=\"a\"", "class=\"b\"", "data-x=\"c\""):
            self.assertIn(attr, out,
                          f"{attr} must survive unedited render")
