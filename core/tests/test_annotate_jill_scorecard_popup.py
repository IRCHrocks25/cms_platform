"""CMS-81: make Jill Gentry's Scorecard choices editable without rewriting them."""

from io import StringIO

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from core.management.commands.annotate_jill_scorecard_popup import (
    annotate_scorecard_popup,
)
from core.models import CustomDomain, Template, Tenant
from core.parser import build_schema
from core.renderer import render_site


HTML = """<!doctype html><html><body>
<button data-scorecard>Take the Scorecard</button>
<div id="scorecardModal" role="dialog" class="modal-overlay">
  <p data-edit="auto.n9" data-type="text">The Move-In Method Scorecard</p>
  <div class="roles">
    <a class="role role--primary" data-quiz="corporate" href="https://example.com/corporate" target="_blank" rel="noopener noreferrer"><svg><path d="M1 1"/></svg><span class="r-body"><span class="r-title">Corporate Sales Director</span><span class="r-sub">Several communities</span></span></a>
    <a class="role" data-quiz="executive" href="https://example.com/executive" target="_blank" rel="noopener noreferrer"><svg><path d="M2 2"/></svg><span class="r-body"><span class="r-title">Executive Director</span><span class="r-sub">One community</span></span></a>
    <a class="role" data-quiz="sales" href="https://example.com/sales" target="_blank" rel="noopener noreferrer"><svg><path d="M3 3"/></svg><span class="r-body"><span class="r-title">Community Sales Director</span><span class="r-sub">Pipeline</span></span></a>
  </div>
  <p data-edit="auto.n12" data-type="text">Not sure? Start here.</p>
</div></body></html>"""


class ScorecardAnnotationTests(SimpleTestCase):
    def test_fields_and_existing_markup_survive(self):
        updated = annotate_scorecard_popup(HTML)
        soup = BeautifulSoup(updated, "lxml")
        self.assertEqual(soup.select_one(".roles")["data-section"], "scorecard_popup")
        self.assertEqual(
            [field["id"] for section in build_schema(updated)["sections"]
             if section["id"] == "scorecard_popup" for field in section["fields"]],
            ["scorecard_popup.corporate_url", "scorecard_popup.corporate_label",
             "scorecard_popup.executive_url", "scorecard_popup.executive_label",
             "scorecard_popup.sales_url", "scorecard_popup.sales_label"],
        )
        self.assertEqual(soup.select_one("[data-quiz=corporate] .r-title").text,
                         "Corporate Sales Director")
        self.assertEqual(len(soup.select(".roles svg path")), 3)
        self.assertEqual(soup.select_one("[data-edit='auto.n9']").text,
                         "The Move-In Method Scorecard")
        self.assertEqual(soup.select_one("[data-edit='auto.n12']").text,
                         "Not sure? Start here.")
        self.assertEqual(annotate_scorecard_popup(updated), updated)

    def test_label_and_url_render_independently(self):
        updated = annotate_scorecard_popup(HTML)
        rendered = render_site(updated, {
            "scorecard_popup": {
                "corporate_label": "Portfolio Director",
                "corporate_url": "https://example.org/new-quiz",
            }
        })
        soup = BeautifulSoup(rendered, "lxml")
        corporate = soup.select_one("[data-quiz=corporate]")
        self.assertEqual(corporate["href"], "https://example.org/new-quiz")
        self.assertEqual(corporate.select_one(".r-title").text, "Portfolio Director")
        self.assertEqual(corporate.select_one(".r-sub").text, "Several communities")
        self.assertIsNotNone(corporate.select_one("svg path"))
        self.assertEqual(corporate["target"], "_blank")
        self.assertEqual(soup.select_one("[data-quiz=executive]")["href"],
                         "https://example.com/executive")
        self.assertEqual(soup.select_one("[data-quiz=sales] .r-title").text,
                         "Community Sales Director")

    def test_missing_or_ambiguous_role_refuses_annotation(self):
        with self.assertRaises(CommandError):
            annotate_scorecard_popup(HTML.replace('data-quiz="sales"', 'data-quiz="other"'))
        with self.assertRaises(CommandError):
            annotate_scorecard_popup(HTML.replace('data-quiz="sales"', 'data-quiz="corporate"'))
        with self.assertRaises(CommandError):
            annotate_scorecard_popup(HTML.replace('class="r-title"',
                                                  'class="role-name"', 1))
        with self.assertRaises(CommandError):
            annotate_scorecard_popup(HTML.replace('class="roles"',
                                                  'class="roles" data-section="other"'))

    def test_unsafe_url_is_rejected_by_existing_link_policy(self):
        updated = annotate_scorecard_popup(HTML)
        rendered = render_site(updated, {
            "scorecard_popup": {"corporate_url": "javascript:alert(1)"}
        })
        self.assertEqual(
            BeautifulSoup(rendered, "lxml").select_one("[data-quiz=corporate]")["href"],
            "https://example.com/corporate",
        )


class ScorecardCommandTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("jill", password="x")
        template = Template.objects.create(name="Jill homepage", html_source=HTML)
        self.tenant = Tenant.objects.create(
            name="Jill Gentry", subdomain="jill-gentry", template=template,
            owner=owner, content={"auto": {"n9": "Saved heading"}},
        )
        template.tenant = self.tenant
        template.save(update_fields=["tenant"])
        CustomDomain.objects.create(tenant=self.tenant, domain="jillgentryandco.com",
                                    is_verified=True)
        self.template = template

    def test_dry_run_then_apply_is_versioned_and_preserves_content(self):
        before = render_site(self.template.html_source, self.tenant.content)
        call_command("annotate_jill_scorecard_popup", stdout=StringIO())
        self.template.refresh_from_db()
        self.assertEqual(self.template.html_source, HTML)
        self.assertEqual(self.template.versions.count(), 0)

        call_command("annotate_jill_scorecard_popup", apply=True, stdout=StringIO())
        self.template.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertEqual(self.template.versions.count(), 1)
        self.assertEqual(self.tenant.content, {"auto": {"n9": "Saved heading"}})
        self.assertEqual(
            BeautifulSoup(before, "lxml").get_text(" ", strip=True),
            BeautifulSoup(render_site(self.template.html_source, self.tenant.content),
                          "lxml").get_text(" ", strip=True),
        )
        call_command("annotate_jill_scorecard_popup", apply=True, stdout=StringIO())
        self.assertEqual(self.template.versions.count(), 1)

    def test_wrong_domain_or_markup_does_not_write(self):
        CustomDomain.objects.all().delete()
        with self.assertRaises(CommandError):
            call_command("annotate_jill_scorecard_popup", apply=True,
                         stdout=StringIO())
        self.assertEqual(self.template.versions.count(), 0)
