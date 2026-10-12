"""audit_split_glue_scripts: list block shells hit by CMS-69 (read-only)."""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from core.models import BlockType, Template, Tenant

SHELL = (
    "<!doctype html><html><body>"
    "<header>Nav</header>"
    '<main><div data-region="main"></div></main>'
    "<footer>Foot</footer>"
    "<div class='fgm' id='fgModal'>Guide</div>"
    "</body></html>"
)
MOVED = (
    '<section data-section="contact">Contact'
    "<script>var m=document.getElementById('fgModal'); if(!m) return;</script>"
    "</section>"
)
CAROUSEL = (
    '<section data-section="proof"><div id="track"></div>'
    "<script>document.getElementById('track').dataset.ready=1</script>"
    "</section>"
)


class AuditSplitGlueScriptsTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="x")

    def make(self, name, shell, fragments, subdomain):
        tpl = Template.objects.create(name=name, html_source=shell)
        for key, html in fragments:
            bt = BlockType.objects.create(
                template=tpl, key=key, label=key, html_source=html
            )
            tpl.allowed_block_types.add(bt)
        Tenant.objects.create(
            name=name, subdomain=subdomain, template=tpl, owner=self.owner
        )
        return tpl

    def run_audit(self):
        out = StringIO()
        call_command("audit_split_glue_scripts", stdout=out)
        return out.getvalue()

    def test_lists_template_whose_block_script_targets_shell_markup(self):
        tpl = self.make("Compass", SHELL, [("contact", MOVED)], "compass")
        out = self.run_audit()
        self.assertIn(f"pk={tpl.pk}", out)
        self.assertIn("compass", out)
        self.assertIn("contact", out)
        self.assertIn("#fgModal", out)
        self.assertIn("Likely affected: 1 template(s)", out)
        self.assertIn("Needs review: 0 template(s)", out)

    def test_block_script_on_own_markup_is_only_flagged_for_review(self):
        tpl = self.make("Clean", SHELL, [("proof", CAROUSEL)], "clean")
        out = self.run_audit()
        self.assertIn("Likely affected: 0 template(s)", out)
        self.assertIn("Needs review: 1 template(s)", out)
        self.assertIn(f"pk={tpl.pk}", out)

    def test_ignores_shell_without_markup_after_footer(self):
        shell = SHELL.replace("<div class='fgm' id='fgModal'>Guide</div>", "")
        self.make("NoTail", shell, [("contact", MOVED)], "notail")
        out = self.run_audit()
        self.assertIn("Likely affected: 0 template(s)", out)
        self.assertIn("Needs review: 0 template(s)", out)

    def test_does_not_modify_anything(self):
        tpl = self.make("Compass", SHELL, [("contact", MOVED)], "compass")
        self.run_audit()
        tpl.refresh_from_db()
        self.assertEqual(tpl.html_source, SHELL)
        self.assertEqual(
            BlockType.objects.get(template=tpl, key="contact").html_source, MOVED
        )
