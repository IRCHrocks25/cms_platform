"""CMS-72: annotation and conversion must not silently change live content."""

from bs4 import BeautifulSoup, CData, Comment, Declaration, Doctype, ProcessingInstruction
from unittest import mock
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Page, Template, Tenant
from core.services import blocks
from core.services.annotator import (
    AnnotatorError, _apply_annotations, _assert_existing_markers_preserved,
    annotate_html_result,
)
from core.renderer import merge_with_defaults, render_site
from core.parser import build_schema
from core.services.templates import AnnotationMismatchError, _annotation_snapshot, save_template_version


OLD = (
    '<html><body><section data-section="mission" data-label="Mission">'
    '<p data-edit="mission.eyebrow" data-type="text">Real Women. Real Results.</p>'
    '<div><p data-edit="mission.option_a" data-type="text">Option A</p>'
    '<p data-edit="mission.option_b" data-type="text">Option B</p></div>'
    '</section><footer data-section="footer" data-group="Footer">'
    '<a data-edit="footer.about_link" data-type="link" href="#about" '
    'title="About section">About Kieran</a></footer></body></html>'
)


class ModelAnnotationPreservationTests(TestCase):
    def test_model_cannot_rename_existing_ids_or_retype_existing_fields(self):
        soup = BeautifulSoup(OLD, "html.parser")
        refs = {str(n): tag for n, tag in enumerate(soup.find_all(True))}
        for ref, tag in refs.items():
            tag["data-cms-ref"] = ref
        mission = soup.find("section")
        eyebrow = mission.find("p")
        link = soup.find("a")
        model = {
            "sections": [{"ref": int(mission["data-cms-ref"]), "id": "new_mission"}],
            "fields": [
                {"ref": int(eyebrow["data-cms-ref"]), "edit": "mission.option_a", "type": "image"},
                {"ref": int(link["data-cms-ref"]), "edit": "footer.link_1_2", "type": "text"},
            ],
        }
        _apply_annotations(refs, model)
        self.assertEqual(mission["data-section"], "mission")
        self.assertEqual(eyebrow["data-edit"], "mission.eyebrow")
        self.assertEqual(eyebrow["data-type"], "text")
        self.assertEqual(link["data-edit"], "footer.about_link")
        self.assertEqual(link["href"], "#about")
        self.assertEqual(link["title"], "About section")

    def test_model_cannot_put_image_field_on_text_wrapper(self):
        soup = BeautifulSoup('<section data-section="hero"><div><p>Copy</p></div></section>', "html.parser")
        wrapper = soup.div
        _apply_annotations({"3": wrapper}, {"fields": [
            {"ref": 3, "edit": "hero.image_2", "type": "image"}
        ]})
        self.assertIsNone(wrapper.get("data-edit"))

    def test_nested_section_cannot_take_an_existing_field_owner(self):
        changed = OLD.replace('<div><p data-edit="mission.option_a"',
                              '<div data-section="new_owner"><p data-edit="mission.option_a"')
        with self.assertRaises(AnnotatorError):
            _assert_existing_markers_preserved(OLD, changed)

    @override_settings(OPENAI_API_KEY="test", OPENAI_ANNOTATE_MODEL="test-model")
    def test_repeated_model_pass_keeps_ids_and_page_copy(self):
        html = OLD
        for _ in range(2):
            source = BeautifulSoup(html, "html.parser")
            tags = source.find_all(True)
            mission = source.find("section")
            eyebrow = mission.find("p")
            link = source.find("a")
            proposal = {
                "sections": [{"ref": tags.index(mission), "id": "new_mission"}],
                "fields": [
                    {"ref": tags.index(eyebrow), "edit": "mission.option_a", "type": "image"},
                    {"ref": tags.index(link), "edit": "footer.link_1_2", "type": "link"},
                ],
            }
            with mock.patch("core.services.annotator._make_openai_client"), mock.patch(
                "core.services.annotator._annotate_chunks_parallel", return_value=[proposal]
            ):
                html = annotate_html_result(html).html
            parsed = BeautifulSoup(html, "html.parser")
            self.assertEqual(parsed.find("section")["data-section"], "mission")
            self.assertEqual(parsed.find("section").find("p")["data-edit"], "mission.eyebrow")
            self.assertEqual(parsed.find("a")["data-edit"], "footer.about_link")
            self.assertEqual(parsed.find("a")["href"], "#about")
            self.assertIn("Real Women. Real Results.", html)


class PreSaveComparisonTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cms72", password="pw")
        self.template = Template.objects.create(name="Mission", html_source=OLD)
        self.tenant = Tenant.objects.create(
            name="Kieran copy", subdomain="kieran-copy", template=self.template,
            owner=self.user, content={"mission": {"eyebrow": "Client eyebrow"},
                                      "footer": {"about_link": "#about"}},
        )

    def test_stale_override_collision_rejected_before_version_or_content_write(self):
        candidate = OLD.replace('data-edit="mission.eyebrow"', 'data-edit="mission.option_a"')
        candidate = candidate.replace('data-edit="mission.option_a" data-type="text">Option A',
                                      'data-edit="mission.eyebrow" data-type="text">Option A')
        before = self.tenant.content.copy()
        with self.assertRaises(AnnotationMismatchError):
            save_template_version(self.template, candidate, user=self.user, preserve_rendered=True)
        self.template.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertEqual(self.template.html_source, OLD)
        self.assertEqual(self.tenant.content, before)
        self.assertEqual(self.template.versions.count(), 0)

    def test_link_href_and_unintended_text_change_are_rejected(self):
        for candidate in (OLD.replace('href="#about"', 'href=""'),
                          OLD.replace('Real Women. Real Results.', 'Option AOption BOption C')):
            with self.subTest(candidate=candidate[:80]), self.assertRaises(AnnotationMismatchError):
                save_template_version(self.template, candidate, user=self.user, preserve_rendered=True)

    def test_annotation_ignores_whitespace_after_document_root(self):
        raw = (
            '<!DOCTYPE html><html><body><section><p>Safe copy</p>'
            '</section></body></html>\n'
        )
        annotated = (
            '<!DOCTYPE html><html><body><section data-section="intro">'
            '<p data-edit="intro.title" data-type="text">Safe copy</p>'
            '</section></body></html>'
        )
        template = Template.objects.create(name="Synthetic whitespace", html_source=raw)

        save_template_version(template, annotated, user=self.user, preserve_rendered=True)

        template.refresh_from_db()
        self.assertEqual(template.html_source, annotated)
        self.assertEqual(_annotation_snapshot(raw, {}), _annotation_snapshot(annotated, {}))

    def test_annotation_rejects_whitespace_only_comment_after_document_root(self):
        raw = '<html><body><p>Safe copy</p></body></html>'
        template = Template.objects.create(name="Synthetic comment", html_source=raw)

        with self.assertRaises(AnnotationMismatchError):
            save_template_version(template, raw + '<!-- -->', user=self.user, preserve_rendered=True)

        template.refresh_from_db()
        self.assertEqual(template.html_source, raw)

    def test_document_edge_trim_preserves_special_string_nodes(self):
        raw = '<html><body><p>Safe copy</p></body></html>'
        baseline = _annotation_snapshot(raw, {})
        for node_type in (Comment, Declaration, Doctype, CData, ProcessingInstruction):
            for edge in ("before", "after"):
                with self.subTest(node=node_type.__name__, edge=edge):
                    soup = BeautifulSoup(raw, "lxml")
                    node = node_type(" ")
                    if edge == "before":
                        soup.insert(0, node)
                    else:
                        soup.append(node)
                    with mock.patch("core.services.templates.BeautifulSoup", return_value=soup):
                        self.assertNotEqual(_annotation_snapshot(raw, {}), baseline)

    def test_attribute_only_annotation_preserves_content_and_repeat_save(self):
        candidate = OLD.replace('<p data-edit="mission.eyebrow"',
                                '<p data-label="Eyebrow" data-edit="mission.eyebrow"')
        save_template_version(self.template, candidate, user=self.user, preserve_rendered=True)
        first = self.tenant.content.copy()
        save_template_version(self.template, candidate, user=self.user, preserve_rendered=True)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, first)
        self.assertEqual(self.template.versions.count(), 1)


class ConversionGateTests(TestCase):
    @override_settings(STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    })
    def test_editor_visibly_warns_and_keeps_flat_page_when_conversion_differs(self):
        user = User.objects.create_user("operator", password="pw", is_staff=True)
        template = Template.objects.create(name="Unsafe", html_source=OLD)
        tenant = Tenant.objects.create(name="Unsafe", subdomain="unsafe", owner=user,
                                       template=template, content={"mission": {"eyebrow": "Client"}})
        self.client.force_login(user)
        with mock.patch("core.services.blocks.classic_upgrade_is_safe",
                        return_value=(False, "mission eyebrow mismatch")):
            response = self.client.get(reverse("dashboard:tenant_editor", args=[tenant.pk]))
        tenant.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Block conversion refused before Save")
        self.assertFalse(tenant.template.is_block_shell)

    def test_flat_to_block_and_two_repeat_runs_keep_composed_dom_and_content(self):
        user = User.objects.create_user("roundtrip", password="pw")
        template = Template.objects.create(name="Roundtrip", html_source=OLD)
        content = {"mission": {"eyebrow": "Client eyebrow"},
                   "footer": {"about_link": "#about"}}
        tenant = Tenant.objects.create(name="Roundtrip", subdomain="roundtrip", owner=user,
                                       template=template, content=content)
        before = render_site(OLD, merge_with_defaults(build_schema(OLD), content))
        for _ in range(3):
            blocks.ensure_block_editor(tenant)
            tenant.refresh_from_db()
            rendered = blocks.render_content(tenant.template, tenant.content)
            self.assertEqual(blocks.normalize_for_diff(rendered),
                             blocks.normalize_for_diff(before))
            composed = BeautifulSoup(rendered, "lxml")
            self.assertEqual(composed.find(attrs={"data-edit": "mission.eyebrow"}).get_text(strip=True),
                             "Client eyebrow")
            about = composed.find(attrs={"data-edit": "footer.about_link"})
            self.assertEqual(about["href"], "#about")
            self.assertEqual(about["title"], "About section")
            self.assertEqual(tenant.content["_classic"], content)
            self.assertEqual(tenant.content["regions"]["main"][0]["fields"]["eyebrow"],
                             "Client eyebrow")
        self.assertEqual(tenant.template.own_block_types.filter(key="mission").count(), 1)

    def test_refuses_mismatched_conversion_without_changing_records(self):
        user = User.objects.create_user("convert", password="pw")
        template = Template.objects.create(name="Flat", html_source=OLD)
        tenant = Tenant.objects.create(name="Flat", subdomain="flat", owner=user,
                                       template=template, content={"mission": {"eyebrow": "Client"}})
        original = tenant.content.copy()
        from unittest.mock import patch
        with patch("core.services.blocks.classic_upgrade_is_safe", return_value=(False, "text mismatch")):
            with self.assertRaises(blocks.ConversionMismatchError):
                blocks.ensure_block_editor(tenant)
        tenant.refresh_from_db()
        template.refresh_from_db()
        self.assertEqual(tenant.content, original)
        self.assertEqual(template.html_source, OLD)
        self.assertFalse(template.own_block_types.exists())


class AutoOverrideAnnotationMigrationTests(TestCase):
    """CMS-84: AI markers must not move edits made through auto.n slots."""

    RAW = (
        '<html><body><section><h1>Default title</h1>'
        '<p>Default body</p><p>Second body</p></section></body></html>'
    )
    ANNOTATED = RAW.replace('<section>', '<section data-section="hero">').replace(
        '<h1>', '<h1 data-edit="hero.title" data-type="text">'
    )

    def setUp(self):
        self.user = User.objects.create_user('cms84', password='pw', is_staff=True)
        self.template = Template.objects.create(name='Synthetic auto slots', html_source=self.RAW)
        self.tenant = Tenant.objects.create(
            name='Synthetic', subdomain='cms84-synthetic', template=self.template,
            owner=self.user, content={'auto': {'n0': 'Edited title', 'n1': 'Edited body'}},
            is_published=True,
        )

    def test_migrates_edited_auto_fields_to_same_elements_and_keeps_render(self):
        before = _annotation_snapshot(self.RAW, self.tenant.content)
        save_template_version(
            self.template, self.ANNOTATED, user=self.user, preserve_rendered=True,
        )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content['hero']['title'], 'Edited title')
        self.assertEqual(self.tenant.content['auto']['n0'], 'Edited body')
        self.assertEqual(_annotation_snapshot(self.ANNOTATED, self.tenant.content), before)
        self.assertEqual(self.tenant.versions.count(), 1)
        self.assertEqual(self.tenant.versions.get(page__isnull=True).snapshot['auto']['n0'], 'Edited title')

    def test_migrates_page_override_and_keeps_page_snapshot(self):
        page = Page.objects.create(
            tenant=self.tenant, template=self.template, title='Synthetic page',
            slug='synthetic-page', content={'auto': {'n1': 'Edited page body'}},
            is_published=True,
        )
        before = _annotation_snapshot(self.RAW, page.content)
        save_template_version(
            self.template, self.ANNOTATED, user=self.user, preserve_rendered=True,
        )
        page.refresh_from_db()
        self.assertEqual(page.content['auto']['n0'], 'Edited page body')
        self.assertEqual(_annotation_snapshot(self.ANNOTATED, page.content), before)
        self.assertEqual(self.tenant.versions.filter(page=page).count(), 1)
        self.assertEqual(self.tenant.versions.get(page=page).snapshot['auto']['n1'], 'Edited page body')

    def test_unmapped_auto_override_rejects_with_field_name_and_rolls_back(self):
        self.tenant.content = {'auto': {'n1': 'Edited body'}}
        self.tenant.save(update_fields=['content'])
        candidate = self.ANNOTATED.replace('<p>Default body</p>', '')
        with self.assertRaisesRegex(AnnotationMismatchError, r'auto\.n1'):
            save_template_version(
                self.template, candidate, user=self.user, preserve_rendered=True,
            )
        self.tenant.refresh_from_db()
        self.template.refresh_from_db()
        self.assertEqual(self.template.html_source, self.RAW)
        self.assertEqual(self.tenant.content['auto']['n1'], 'Edited body')
        self.assertEqual(self.tenant.versions.count(), 0)

    @override_settings(STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    })
    def test_template_form_saves_migrated_annotation_and_keeps_editor_reload(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('dashboard:template_detail', args=[self.template.pk]),
            {
                'name': self.template.name,
                'description': '',
                'editing_mode': self.template.editing_mode,
                'html_source': self.ANNOTATED,
                'preserve_annotated_render': '1',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.template.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertEqual(self.template.html_source, self.ANNOTATED)
        self.assertEqual(self.tenant.content['hero']['title'], 'Edited title')
        self.assertContains(
            self.client.get(reverse('dashboard:template_detail', args=[self.template.pk])),
            'hero.title',
        )

    def test_default_render_change_still_rejects_despite_migration(self):
        self.tenant.content = {}
        self.tenant.save(update_fields=['content'])
        candidate = self.ANNOTATED.replace('Default title', 'Different title')
        with self.assertRaisesRegex(AnnotationMismatchError, 'template defaults'):
            save_template_version(
                self.template, candidate, user=self.user, preserve_rendered=True,
            )
        self.template.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertEqual(self.template.html_source, self.RAW)
        self.assertEqual(self.tenant.content, {})

    def test_conflicting_stable_override_rejects_without_losing_either(self):
        self.tenant.content['hero'] = {'title': 'Existing different edit'}
        self.tenant.save(update_fields=['content'])
        with self.assertRaisesRegex(AnnotationMismatchError, r'auto\.n0'):
            save_template_version(
                self.template, self.ANNOTATED, user=self.user, preserve_rendered=True,
            )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content['hero']['title'], 'Existing different edit')
        self.assertEqual(self.tenant.content['auto']['n0'], 'Edited title')

    def test_second_site_conflict_leaves_first_site_and_template_untouched(self):
        other = Tenant.objects.create(
            name='Second synthetic', subdomain='cms84-second', template=self.template,
            owner=self.user,
            content={
                'auto': {'n0': 'Second title'},
                'hero': {'title': 'Conflicting stable title'},
            },
            is_published=True,
        )
        first_before = self.tenant.content.copy()
        with self.assertRaisesRegex(AnnotationMismatchError, r'auto\.n0'):
            save_template_version(
                self.template, self.ANNOTATED, user=self.user, preserve_rendered=True,
            )
        self.template.refresh_from_db()
        self.tenant.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.template.html_source, self.RAW)
        self.assertEqual(self.tenant.content, first_before)
        self.assertEqual(other.content['auto']['n0'], 'Second title')
        self.assertEqual(self.tenant.versions.count(), 0)
