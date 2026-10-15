"""CMS-92: page layouts keep one tenant-scoped, filtered, sorted page."""
from bs4 import BeautifulSoup
from django.test import TestCase
from django.urls import reverse
from core.models import BLOG_INDEX_LAYOUT_IDS, Tenant, BlogPost
from core.services import blog_render
from core.tests.test_blog import _BlogSiteMixin, _NO_MANIFEST, _post, _make_template

NEW = ('text_only', 'cards_2', 'cards_3', 'featured')
ALL = (*NEW, 'image_left', 'image_right', 'image_above')

@_NO_MANIFEST
class BlogPageLayoutTests(_BlogSiteMixin, TestCase):
    def test_all_explicit_choices_save_and_preview_without_saving(self):
        _post(self.tenant, 'Demo', cover_image='https://example.invalid/demo.png')
        for layout in ALL:
            with self.subTest(layout=layout):
                self.assertIn(layout, BLOG_INDEX_LAYOUT_IDS)
                self._set_settings(index_layout='style')
                response = self._tenant().get(reverse('dashboard:blog_index_preview_self'), {'index_layout': layout})
                self.assertContains(response, 'lst--'+layout)
                self.tenant.refresh_from_db()
                self.assertEqual(self.tenant.blog_settings['index_layout'], 'style')
                response = self._tenant().post(reverse('dashboard:blog_settings_self'), {'index_layout': layout, 'template': 'minimal'})
                self.assertEqual(response.status_code, 302)
                self.tenant.refresh_from_db()
                self.assertEqual(self.tenant.blog_settings['index_layout'], layout)
                self.assertContains(self._tenant().get(reverse('dashboard:blog_list_self')), f'value="{layout}" selected')

    def test_new_layout_structure_and_missing_covers(self):
        _post(self.tenant, 'Without')
        _post(self.tenant, 'With', cover_image='https://example.invalid/demo.png')
        for layout in NEW:
            with self.subTest(layout=layout):
                self._set_settings(index_layout=layout)
                soup = BeautifulSoup(self._index(), 'html.parser')
                cards = soup.select('.page-post')
                self.assertEqual(len(cards), 2)
                self.assertEqual(len(soup.select('.page-cover')), 0 if layout=='text_only' else 1)
                self.assertEqual(len(soup.select('.page-post--no-cover')), 1)
                for img in soup.select('.page-cover'):
                    self.assertEqual(img['alt'], '')
                    self.assertEqual(img.parent['tabindex'], '-1')

    def test_featured_positions_follow_current_page_order(self):
        for i in range(12): _post(self.tenant, f'Demo {i:02d}')
        self._set_settings(index_layout='featured')
        for page,expected in [(1,list(range(11,2,-1))),(2,[2,1,0])]:
            soup = BeautifulSoup(self._index('?page='+str(page)), 'html.parser')
            cards=soup.select('.page-post')
            self.assertEqual([c.h2.get_text(strip=True) for c in cards],[f'Demo {i:02d}' for i in expected])
            self.assertIn('page-post--lead',cards[0]['class'])
            self.assertTrue(all('page-post--secondary' in c['class'] for c in cards[1:3]))
            self.assertTrue(all('page-post--grid' in c['class'] for c in cards[3:]))

    def test_filter_sort_pagination_and_tenant_scope_for_every_layout(self):
        for i in range(12): _post(self.tenant,f'News {i:02d}',category='News')
        _post(self.tenant,'Wrong category',category='Guides')
        _post(self.tenant,'Draft secret',category='News',status=BlogPost.STATUS_DRAFT)
        other=Tenant.objects.create(name='Other',subdomain='other',template=_make_template(),owner=self.staff,is_published=True)
        _post(other,'Other tenant secret',category='News')
        for layout in ALL:
            with self.subTest(layout=layout):
                self._set_settings(index_layout=layout)
                first=self._index('?category=news&sort=oldest')
                self.assertNotIn('Wrong category',first)
                self.assertNotIn('Draft secret',first)
                self.assertNotIn('Other tenant secret',first)
                self.assertIn('category=news&amp;sort=oldest&amp;page=2',first)
                bodies=[first,self._index('?category=news&sort=oldest&page=2')]
                titles=[]
                for body in bodies:
                    titles.extend(a.get_text(strip=True) for a in BeautifulSoup(body,'html.parser').select('.cms-blog article h2 a'))
                self.assertEqual(titles,[f'News {i:02d}' for i in range(12)])

    def test_preview_remains_tenant_authorized(self):
        response=self._tenant(self.outsider).get(reverse('dashboard:blog_index_preview_self'),{'index_layout':'featured'})
        self.assertEqual(response.status_code,403)

    def test_unset_equals_explicit_style_byte_for_byte(self):
        _post(self.tenant,'Original',cover_image='https://example.invalid/demo.png')
        for style in ['minimal','magazine','cards']:
            self._set_settings(template=style)
            self.tenant.blog_settings.pop('index_layout',None)
            self.tenant.save(update_fields=['blog_settings'])
            unset=self._index()
            self._set_settings(index_layout='style')
            self.assertEqual(self._index(),unset)

# Real browser acceptance stays synthetic and local; no installed tenant writes.
import os
import unittest
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from core.models import Template
from core.tests.test_blog import _SerialLiveServerThread, _opt_in_to_site_blog_fonts
try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None

@unittest.skipIf(sync_playwright is None, 'optional playwright not installed')
@override_settings(TENANT_BASE_DOMAIN='localhost', ALLOWED_HOSTS=['*'],
    STORAGES={'default': {'BACKEND':'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class BlogPageLayoutBrowserTests(StaticLiveServerTestCase):
    server_thread_class = _SerialLiveServerThread

    def test_layouts_and_keyboard_preview_at_both_widths(self):
        os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE','true')
        owner=get_user_model().objects.create_user('page-demo',is_staff=True)
        template=Template.objects.create(name='Synthetic layout test',html_source='''
            <html><head><style>:root{--primary:#ff2e88;--font-body:Inter,sans-serif;--font-heading:'Bebas Neue',sans-serif}
            body{margin:0} main{max-width:1100px;margin:auto}</style></head>
            <body><header>Demo</header><main><h1 data-edit="hero.title" data-type="text">Demo</h1></main><footer>Demo</footer></body></html>''')
        tenant=Tenant.objects.create(name='Demo',subdomain='page-demo',template=template,owner=owner,is_published=True)
        _opt_in_to_site_blog_fonts(tenant)
        for i in range(10):
            _post(tenant,f'Demo {i:02d}',category='News',cover_image='/demo-cover.svg' if i%2 else '',excerpt='Synthetic excerpt for layout acceptance.')
        self.client.force_login(owner)
        with sync_playwright() as pw:
            browser=pw.chromium.launch()
            try:
                context=browser.new_context()
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                context.route('**/demo-cover.svg',lambda route:route.fulfill(content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect width="800" height="500" fill="#2457d6"/></svg>'))
                context.route('https://**',lambda route:route.abort())
                page=context.new_page()
                for width in [390,1280]:
                    page.set_viewport_size({'width':width,'height':900})
                    for layout in ALL:
                        tenant.blog_settings={'index_layout':layout,'template':'minimal'}
                        tenant.save(update_fields=['blog_settings'])
                        self.assertEqual(page.goto(self.live_server_url+'/site/page-demo/blog/').status,200)
                        expect(page.locator('.lst--'+layout)).to_be_visible()
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                        if layout in NEW:
                            self.assertIn('Inter',page.locator('.cms-blog').evaluate('e=>getComputedStyle(e).fontFamily'))
                            title=page.locator('.page-title').first
                            self.assertIn('Bebas Neue',title.evaluate('e=>getComputedStyle(e).fontFamily'))
                            link=title.locator('a');link.focus()
                            self.assertEqual(link.evaluate('e=>getComputedStyle(e).outlineColor'),'rgb(255, 46, 136)')
                            self.assertGreater(link.evaluate('e=>parseFloat(getComputedStyle(e).outlineWidth)'),0)
                            cards=page.locator('.page-post');boxes=[cards.nth(i).bounding_box() for i in range(min(4,cards.count()))]
                            if width==390:
                                self.assertTrue(all(abs(b['x']-boxes[0]['x'])<2 for b in boxes))
                            elif layout=='featured':
                                self.assertGreater(boxes[0]['width'],boxes[1]['width'])
                                self.assertAlmostEqual(boxes[1]['x'],boxes[2]['x'],delta=2)
                                self.assertGreater(boxes[2]['y'],boxes[1]['y'])
                                self.assertGreater(boxes[3]['y'],boxes[2]['y'])
                            elif layout.startswith('cards_'):
                                cols=int(layout[-1]);self.assertAlmostEqual(boxes[0]['y'],boxes[cols-1]['y'],delta=2)
                        if os.getenv('CMS92_SCREENSHOTS') and layout in ['featured','cards_3','text_only']:
                            page.screenshot(path=os.path.join(os.environ['CMS92_SCREENSHOTS'],f'{layout}-{width}.png'),full_page=True)
                    tenant.blog_settings={'template':'minimal'};tenant.save(update_fields=['blog_settings'])
                    self.assertEqual(page.goto(self.live_server_url+reverse('dashboard:blog_list',args=[tenant.pk])).status,200)
                    picker=page.get_by_label('Post list layout',exact=True)
                    expect(page.frame_locator('#index-preview-frame').locator('.cms-blog')).to_be_visible()
                    picker.focus();page.keyboard.press('End');page.keyboard.press('Tab')
                    expect(picker).to_have_value('featured')
                    expect(page.frame_locator('#index-preview-frame').locator('.lst--featured')).to_be_visible()
                    self.assertEqual(page.locator('#index-preview-frame').get_attribute('title'),'Blog page preview')
                    tenant.refresh_from_db();self.assertNotIn('index_layout',tenant.blog_settings)
                    for layout in ALL:
                        picker.select_option(layout)
                        expect(page.frame_locator('#index-preview-frame').locator('.lst--'+layout)).to_be_visible()
            finally:
                browser.close()
