"""CMS-93: explicit Front Line homepage strip, with legacy output preserved."""
import unittest
from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import BlogPost, Page, Template, Tenant

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@override_settings(TENANT_BASE_DOMAIN="localhost")
class FrontLineStripTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(
            "front-line-operator", password="test-only", is_staff=True,
        )
        cls.template = Template.objects.create(
            name="Front Line synthetic",
            html_source=(
                "<html><head><style>:root{--primary:#ff2e88;"
                "--font-heading:'Bebas Neue',sans-serif;"
                "--font-body:'Inter',sans-serif}</style></head>"
                "<body><main><section id='blog' data-section='blog'>"
                "<div data-blog-strip></div></section></main>"
                "<footer data-section='footer'>Footer</footer></body></html>"
            ),
        )
        cls.tenant = Tenant.objects.create(
            name="Synthetic", subdomain="front-line-synthetic",
            template=cls.template, owner=cls.owner, is_published=True,
            blog_settings={"strip_style": "cards", "strip_count": 3},
        )
        cls.first = BlogPost.objects.create(
            tenant=cls.tenant, title="Unsafe <title> & one", slug="one",
            category="<Unsafe & category>", excerpt="<Unsafe & excerpt>",
            body="<p>" + "word " * 201 + "</p>",
            cover_image="https://example.test/cover.jpg", status="published",
            publish_date=timezone.now(), featured=True, featured_order=0,
        )
        cls.second = BlogPost.objects.create(
            tenant=cls.tenant, title="Second", slug="two", category="",
            body="<p>brief</p>", status="published",
            publish_date=timezone.now(), featured=True, featured_order=1,
        )

    def _home(self):
        response = Client(HTTP_HOST="front-line-synthetic.localhost").get("/")
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.content, "html.parser")

    def test_global_selection_renders_cards_from_posts_with_safe_metadata(self):
        self.tenant.blog_settings = {
            **self.tenant.blog_settings, "strip_style": "front_line",
            "strip_heading": "From the Front Line", "strip_eyebrow": "What's working now",
            "strip_subtitle": "Fresh thinking",
        }
        self.tenant.save(update_fields=["blog_settings"])
        html = str(self._home().select_one("#blog [data-cms-blog-strip]"))
        soup = BeautifulSoup(html, "html.parser")
        self.assertIn("cms-strip--front-line", soup.section.get("class", []))
        self.assertEqual(len(soup.select(".fl-card")), 2)
        self.assertEqual([n.get_text(strip=True) for n in soup.select(".fl-number")], ["01", "02"])
        self.assertEqual(len(soup.select(".fl-category")), 1)
        self.assertIn("2 min read", soup.select_one(".fl-card:first-child .fl-reading").get_text().lower())
        self.assertIn("1 min read", soup.select_one(".fl-card:nth-child(2) .fl-reading").get_text().lower())
        self.assertIn("Fresh thinking", soup.select_one(".fl-subtitle").get_text())
        self.assertIn("&lt;Unsafe &amp; category&gt;", html)
        self.assertIn("Unsafe &lt;title&gt; &amp; one", html)
        self.assertIn("&lt;Unsafe &amp; excerpt&gt;", html)
        self.assertNotIn("<Unsafe", html)
        self.assertNotIn("<title>", html)

    def test_per_marker_choice_overrides_global_and_settings_control_saves(self):
        agency = Client(HTTP_HOST="localhost")
        agency.force_login(self.owner)
        settings_page = agency.get(reverse("dashboard:blog_list", args=[self.tenant.pk]))
        self.assertContains(settings_page, 'value="front_line"')
        self.assertContains(settings_page, 'name="strip_layout_home_0"')
        response = agency.post(reverse("dashboard:blog_settings", args=[self.tenant.pk]), {
            "template": "minimal", "index_layout": "style", "title": "Blog",
            "strip_style": "cards", "strip_count": "3", "strip_enabled": "on",
            "strip_heading": "From the Front Line", "strip_subtitle": "Fresh thinking",
            "strip_eyebrow": "What's working now", "strip_layout_home_0": "front_line",
        })
        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.blog_settings["strip_layouts"]["home_0"], "front_line")
        self.assertEqual(self.tenant.blog_settings["strip_style"], "cards")
        strip = self._home().select_one("#blog [data-cms-blog-strip]")
        self.assertIn("cms-strip--front-line", strip.get("class", []))
        self.assertIn("Fresh thinking", strip.get_text())

    def test_mount_inside_existing_blog_header_has_one_header(self):
        template = Template.objects.create(
            name="Header-owned mount",
            html_source=(
                "<html><body><section id='blog' data-section='blog'>"
                "<p class='tag'>Existing eyebrow</p><h2>Existing heading</h2>"
                "<p class='shift-support'>Existing subtitle</p>"
                "<div data-blog-strip data-blog-strip-header='none'></div>"
                "</section><footer>End</footer></body></html>"
            ),
        )
        tenant = Tenant.objects.create(
            name="Header mount", subdomain="header-front-line", template=template,
            owner=self.owner, is_published=True,
            blog_settings={"strip_style": "cards", "strip_count": 1,
                           "strip_layouts": {"home_0": "front_line"}},
        )
        BlogPost.objects.create(
            tenant=tenant, title="Example", slug="example", body="One word",
            status="published", publish_date=timezone.now(), featured=True,
        )
        response = Client(HTTP_HOST="header-front-line.localhost").get("/")
        self.assertEqual(response.status_code, 200)
        section = BeautifulSoup(response.content, "html.parser").select_one("#blog")
        self.assertEqual(len(section.select("h2")), 1)
        self.assertEqual(len(section.select(".tag")), 1)
        self.assertEqual(len(section.select(".shift-support")), 1)
        self.assertEqual(len(section.select("[data-cms-blog-strip]")), 1)
        self.assertEqual(len(section.select(".fl-heading, .fl-eyebrow, .fl-subtitle")), 0)
        self.assertEqual(len(section.select(".fl-card")), 1)
        self.assertIn("cms-strip--embedded", section.select_one("[data-cms-blog-strip]").get("class", []))

    def test_editor_home_preview_renders_saved_front_line_mount_like_public(self):
        self.template.html_source = self.template.html_source.replace(
            "<div data-blog-strip></div>",
            "<div data-blog-strip data-blog-strip-header='none'></div>",
        )
        self.template.save()
        self.tenant.blog_settings = {
            **self.tenant.blog_settings,
            "strip_layouts": {"home_0": "front_line"},
        }
        self.tenant.save(update_fields=["blog_settings"])
        agency = Client(HTTP_HOST="localhost")
        agency.force_login(self.owner)
        response = agency.get(reverse("dashboard:tenant_preview", args=[self.tenant.pk]))
        self.assertEqual(response.status_code, 200)
        preview = BeautifulSoup(response.content, "html.parser")
        public = self._home()
        for surface in (preview, public):
            blog = surface.select_one("#blog")
            self.assertEqual(len(blog.select("[data-cms-blog-strip]")), 1)
            self.assertEqual(len(blog.select(".fl-card")), 2)
            self.assertEqual(len(blog.select(".fl-heading,.fl-eyebrow")), 0)
        self.assertEqual(
            preview.select_one("#blog [data-cms-blog-strip]").decode_contents().replace(
                f"/site/{self.tenant.subdomain}/blog/", "/blog/"
            ),
            public.select_one("#blog [data-cms-blog-strip]").decode_contents(),
        )

    def test_editor_preview_marks_empty_mount_as_example_without_public_posts(self):
        BlogPost.objects.filter(tenant=self.tenant).update(status="draft")
        self.tenant.blog_settings = {
            **self.tenant.blog_settings,
            "strip_layouts": {"home_0": "front_line"},
        }
        self.tenant.save(update_fields=["blog_settings"])
        agency = Client(HTTP_HOST="localhost")
        agency.force_login(self.owner)
        response = agency.get(reverse("dashboard:tenant_preview", args=[self.tenant.pk]))
        self.assertEqual(response.status_code, 200)
        preview = BeautifulSoup(response.content, "html.parser")
        self.assertEqual(len(preview.select("#blog [data-cms-blog-strip-example]")), 1)
        self.assertIn("Example preview", preview.select_one("#blog [data-cms-blog-strip-example]").text)
        self.assertEqual(len(preview.select("#blog .fl-card")), 0)
        self.assertEqual(len(self._home().select("[data-cms-blog-strip-example]")), 0)
        self.assertEqual(len(self._home().select("[data-cms-blog-strip]")), 0)

        self.tenant.blog_settings = {
            **self.tenant.blog_settings, "strip_enabled": False,
        }
        self.tenant.save(update_fields=["blog_settings"])
        disabled = agency.get(reverse("dashboard:tenant_preview", args=[self.tenant.pk]))
        self.assertNotIn(b"data-cms-blog-strip-example", disabled.content)

    def test_editor_inner_page_preview_uses_page_marker_layout(self):
        page = Page.objects.create(
            tenant=self.tenant, template=self.template, title="Newsroom",
            slug="newsroom", is_published=True,
        )
        self.tenant.blog_settings = {
            **self.tenant.blog_settings,
            "strip_layouts": {"home_0": "image_left", f"page_{page.pk}_0": "front_line"},
        }
        self.tenant.save(update_fields=["blog_settings"])
        agency = Client(HTTP_HOST="localhost")
        agency.force_login(self.owner)
        response = agency.get(reverse("dashboard:page_preview", args=[self.tenant.pk, page.pk]))
        self.assertEqual(response.status_code, 200)
        preview = BeautifulSoup(response.content, "html.parser")
        self.assertEqual(len(preview.select("#blog .cms-strip--front-line")), 1)
        self.assertEqual(len(preview.select("#blog .fl-card")), 2)
        self.assertEqual(len(self._home().select("#blog .cms-strip--front-line")), 0)

    @unittest.skipIf(sync_playwright is None, "playwright is not installed")
    def test_editor_preview_front_line_browser_matches_public_at_both_widths(self):
        self.tenant.blog_settings = {
            **self.tenant.blog_settings,
            "strip_layouts": {"home_0": "front_line"},
        }
        self.tenant.save(update_fields=["blog_settings"])
        agency = Client(HTTP_HOST="localhost")
        agency.force_login(self.owner)
        preview = agency.get(reverse("dashboard:tenant_preview", args=[self.tenant.pk])).content.decode()
        public = Client(HTTP_HOST="front-line-synthetic.localhost").get("/").content.decode()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for width in (1280, 390):
                    with self.subTest(width=width):
                        surfaces = []
                        for html in (preview, public):
                            page = browser.new_page(viewport={"width": width, "height": 900})
                            try:
                                page.set_content(html, wait_until="domcontentloaded")
                                surfaces.append(page.evaluate("""() => {
                                    const blog = document.querySelector('#blog');
                                    const strip = blog.querySelector('.cms-strip--front-line');
                                    const grid = strip.querySelector('.fl-grid');
                                    const title = strip.querySelector('.fl-title');
                                    return {
                                        strips: blog.querySelectorAll('[data-cms-blog-strip]').length,
                                        cards: grid.querySelectorAll('.fl-card').length,
                                        gridColumns: getComputedStyle(grid).gridTemplateColumns,
                                        titleFont: getComputedStyle(title).fontFamily,
                                        titleColor: getComputedStyle(title).color,
                                        overflow: document.documentElement.scrollWidth > innerWidth,
                                    };
                                }"""))
                            finally:
                                page.close()
                        self.assertEqual(surfaces[0], surfaces[1])
                        self.assertEqual(surfaces[0]["strips"], 1)
                        self.assertEqual(surfaces[0]["cards"], 2)
                        self.assertFalse(surfaces[0]["overflow"])
            finally:
                browser.close()

    def test_later_heading_does_not_hide_front_line_header(self):
        template = Template.objects.create(
            name="Later unrelated heading",
            html_source=(
                "<html><body><section data-section='blog'>"
                "<div data-blog-strip></div><h3>Later heading</h3>"
                "</section><footer>End</footer></body></html>"
            ),
        )
        tenant = Tenant.objects.create(
            name="Later heading", subdomain="later-front-line", template=template,
            owner=self.owner, is_published=True,
            blog_settings={"strip_style": "front_line", "strip_count": 1},
        )
        BlogPost.objects.create(
            tenant=tenant, title="Example", slug="example", body="One word",
            status="published", publish_date=timezone.now(), featured=True,
        )
        response = Client(HTTP_HOST="later-front-line.localhost").get("/")
        self.assertEqual(response.status_code, 200)
        section = BeautifulSoup(response.content, "html.parser").select_one("[data-section='blog']")
        self.assertEqual(len(section.select(".fl-heading")), 1)
        self.assertEqual(len(section.select("h3")), 1)
        self.assertNotIn("cms-strip--embedded", section.select_one("[data-cms-blog-strip]").get("class", []))

    def test_unselected_global_and_marker_output_stays_identical(self):
        before = str(self._home().select_one("#blog [data-cms-blog-strip]"))
        self.tenant.blog_settings = {
            **self.tenant.blog_settings,
            "strip_eyebrow": "New option text", "strip_subtitle": "New option subtitle",
            "strip_layouts": {},
        }
        self.tenant.save(update_fields=["blog_settings"])
        after = str(self._home().select_one("#blog [data-cms-blog-strip]"))
        self.assertEqual(after, before)

    def test_unselected_output_across_plain_iam_and_contract_shells(self):
        shells = {
            "plain": "<html><body><main><div data-blog-strip></div></main><footer>End</footer></body></html>",
            "iam": ("<html><head><style>:root{--primary:#ff2e88;--body:Inter,sans-serif;"
                    "--disp:Bebas,sans-serif}</style></head><body><main>"
                    "<section id='blog'><div data-blog-strip></div></section></main>"
                    "<footer>End</footer></body></html>"),
            "contract": ("<html><head><style>:root{--font-body:Arial;--font-heading:Georgia}"
                         "</style></head><body><header>Top</header><main>"
                         "<section data-section='blog'><div data-blog-strip></div></section>"
                         "</main><footer>End</footer></body></html>"),
        }
        for key, shell in shells.items():
            with self.subTest(shell=key):
                template = Template.objects.create(name=key, html_source=shell)
                tenant = Tenant.objects.create(
                    name=key, subdomain=f"front-line-{key}", template=template,
                    owner=self.owner, is_published=True,
                    blog_settings={"strip_style": "cards", "strip_count": 1},
                )
                BlogPost.objects.create(
                    tenant=tenant, title="Stable", slug="stable", body="Body",
                    status="published", publish_date=timezone.now(), featured=True,
                )
                client = Client(HTTP_HOST=f"front-line-{key}.localhost")
                before = str(BeautifulSoup(client.get("/").content, "html.parser"))
                tenant.blog_settings = {
                    **tenant.blog_settings,
                    "strip_eyebrow": "Unused eyebrow", "strip_subtitle": "Unused subtitle",
                    "strip_layouts": {},
                }
                tenant.save(update_fields=["blog_settings"])
                after = str(BeautifulSoup(client.get("/").content, "html.parser"))
                self.assertEqual(after, before)

    def test_global_front_line_opt_in_uses_site_tokens_without_a_mount(self):
        template = Template.objects.create(
            name="Unmarked IAM synthetic",
            html_source=(
                "<html><head><style>:root{--primary:#ff2e88;"
                "--body:'Inter',sans-serif;--disp:'Bebas Neue',sans-serif}"
                "</style></head><body><main><section data-section='blog'>"
                "Blog</section></main><footer>Footer</footer></body></html>"
            ),
        )
        tenant = Tenant.objects.create(
            name="Unmarked", subdomain="unmarked-front-line", template=template,
            owner=self.owner, is_published=True,
            blog_settings={"strip_style": "front_line", "strip_count": 1},
        )
        BlogPost.objects.create(
            tenant=tenant, title="Example", slug="example", body="One word",
            status="published", publish_date=timezone.now(), featured=True,
        )
        response = Client(HTTP_HOST="unmarked-front-line.localhost").get("/")
        self.assertEqual(response.status_code, 200)
        strip = BeautifulSoup(response.content, "html.parser").select_one("[data-cms-blog-strip]")
        self.assertIsNotNone(strip)
        css = strip.style.get_text()
        self.assertIn("var(--primary", css)
        self.assertIn("var(--body", css)
        self.assertIn("var(--disp", css)

    def test_global_preview_honors_new_layout(self):
        agency = Client(HTTP_HOST="localhost")
        agency.force_login(self.owner)
        response = agency.get(
            reverse("dashboard:blog_strip_preview", args=[self.tenant.pk])
            + "?strip_style=front_line&eyebrow=Fresh%20findings&subtitle=New%20thinking"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cms-strip--front-line")
        self.assertContains(response, "Fresh findings")
        self.assertContains(response, "New thinking")
