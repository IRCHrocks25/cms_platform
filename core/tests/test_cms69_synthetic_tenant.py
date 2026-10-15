"""CMS-69 conversion regression on a disposable synthetic tenant."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from core.models import Template, Tenant
from core.services.blocks import ensure_block_editor


@override_settings(TENANT_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["*"])
class FooterScriptTenantTests(TestCase):
    def test_conversion_keeps_popup_initialization_after_modal(self):
        owner = get_user_model().objects.create_user("synthetic-owner")
        source = ("<!doctype html><html><body>"
                  "<header data-section='nav' data-group='Header'>Nav</header>"
                  "<section data-section='hero' data-label='Hero'>Hero</section>"
                  "<footer data-section='footer' data-group='Footer'>Foot</footer>"
                  "<div id='fgModal'>Synthetic guide</div>"
                  "<script>(function(){var m=document.getElementById('fgModal');"
                  "if(m) m.dataset.ready='yes';})();</script></body></html>")
        template = Template.objects.create(name="Synthetic conversion", html_source=source)
        tenant = Tenant.objects.create(name="Synthetic triage", subdomain="triage", owner=owner,
                                       template=template, is_published=True)
        ensure_block_editor(tenant)
        tenant.refresh_from_db()
        self.assertTrue(tenant.template.is_block_shell)
        response = self.client.get("/", HTTP_HOST="triage.localhost")
        self.assertEqual(response.status_code, 200)
        rendered = response.content.decode()
        self.assertLess(rendered.index('id="fgModal"'), rendered.index("getElementById('fgModal')"))
        self.assertTrue(all("getElementById('fgModal')" not in b.html_source
                            for b in tenant.template.own_block_types.all()))
