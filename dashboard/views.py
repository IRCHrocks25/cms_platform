import json
import logging
import re
import secrets
import threading
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_POST, require_GET, require_http_methods


from core.models import (
    CustomDomain, Template, Tenant, TenantMembership, MediaAsset, ContentVersion,
    BlogPost, BLOG_TEMPLATE_CHOICES, BLOG_TEMPLATE_IDS,
    BLOG_STRIP_CHOICES, BLOG_STRIP_IDS, DEFAULT_BLOG_STRIP, _unique_blog_slug,
    Page, RESERVED_PAGE_SLUGS, AnnotationJob, EmbeddableAssistant,
)
from core.permissions import agency_operator_required, agency_admin_required, tenant_member_required
from core.renderer import render_site, merge_with_defaults, strip_defaults
from core.parser import build_schema
from core.services import blog_render
from core.services import custom_domains
from core.services import iceberg_media
from core.services.accounts import (
    create_scoped_login,
    create_tenant_account,
    generate_password,
)
from core.services.annotator import annotate_html, annotate_html_result, AnnotatorError
from core.services.sanitizer import sanitize_html
from core.services import templates as template_svc
from core import ghl_crypto
from core import ghl_oauth
from core.models import GhlAgencyInstall, GhlInstall
from core.services import ghl_connect, ghl_embed_slots, ghl_forms
from core.urls_helpers import build_tenant_url_bundle, tenant_public_url


User = get_user_model()
logger = logging.getLogger(__name__)


def _templates_available(*, tenant=None):
    """Agency library templates, optionally plus one tenant's private copies."""
    if tenant is None:
        return Template.objects.filter(tenant__isnull=True).order_by("name")
    return Template.objects.filter(
        Q(tenant__isnull=True) | Q(tenant=tenant)
    ).order_by("name")


def _client_may_edit_content(request, editable) -> bool:
    """Staff/superuser always; clients only when the template is released."""
    if request.user.is_staff or request.user.is_superuser:
        return True
    tpl = getattr(editable, "template", None)
    return bool(tpl and tpl.is_client_editable)


# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #


STARTER_TEMPLATE_HTML = """\
<main class="starter-page">
  <section class="starter-hero" data-section="hero" data-label="Welcome" data-group="Home">
    <p class="starter-kicker" data-edit="hero.kicker" data-type="text" data-label="Kicker">A clear place to begin</p>
    <h1 class="starter-title" data-edit="hero.title" data-type="text" data-label="Headline">Welcome to your new site</h1>
    <p class="starter-intro" data-edit="hero.intro" data-type="richtext" data-label="Introduction">Tell visitors what you offer and why it matters.</p>
    <img class="starter-image" data-edit="hero.image" data-type="image" data-label="Feature photo"
         src="https://placehold.co/960x540" alt="A bright and welcoming workspace">
    <a class="starter-cta" data-edit="hero.cta" data-type="link" data-label="Primary link" href="#highlight">Explore the details</a>
  </section>

  <section id="highlight" class="starter-highlight" data-section="highlight" data-label="Highlight" data-group="Sections">
    <h2 class="starter-highlight-title" data-edit="highlight.title" data-type="text" data-label="Heading">Make this page your own</h2>
    <div class="starter-highlight-color" data-edit="highlight.background" data-type="color" data-label="Background"
         style="background-color: #dbeafe" aria-label="Highlight background"></div>
  </section>
</main>

<style data-tokens>
  :root {
    --primary: #2563eb;
    --surface: #ffffff;
    --text: #172033;
  }
  .starter-page { color: var(--text); background: var(--surface); font-family: system-ui, sans-serif; }
  .starter-hero, .starter-highlight { max-width: 960px; margin: auto; padding: 64px 24px; }
  .starter-image { display: block; width: 100%; margin: 24px 0; }
  .starter-cta { color: var(--primary); }
  .starter-highlight-color { min-height: 96px; }
</style>
"""


# A blank *block-shell* starter: editable header/footer chrome + a `data-region`
# where the client drops blocks + brand tokens. Because it has a `data-region`,
# `is_block_shell` is True, so assigning it to a site turns on the block editor
# and (with the block library attached) lets the client build the whole site
# from our palette instead of anyone pasting HTML.
BLOCK_SHELL_STARTER_HTML = """\
<header class="site-header" data-section="header" data-label="Header" data-group="Header">
  <div class="site-header-inner">
    <div class="site-header-zone" data-header-zone="left">
      <div class="site-header-brand" data-chrome-piece="brand">
        <a class="site-brand" data-edit="header.brand" data-type="text" data-label="Site name" href="/">Your brand</a>
        <div class="site-header-brand-extra" data-region="header-left"></div>
      </div>
    </div>
    <div class="site-header-zone" data-header-zone="center">
      <nav class="site-nav" data-chrome-piece="nav">
        <div class="site-nav-pages" data-nav-pages></div>
        <div class="site-nav-links" data-region="header-center"></div>
      </nav>
    </div>
    <div class="site-header-zone" data-header-zone="right">
      <div class="site-header-actions" data-chrome-piece="actions" data-region="header-right"></div>
    </div>
  </div>
</header>
<main class="site-main" data-region="main"></main>
<footer class="site-footer" data-section="footer" data-label="Footer" data-group="Footer">
  <div class="site-footer-row">
    <div class="site-footer-col" data-region="footer-left"></div>
    <div class="site-footer-col site-footer-col--center">
      <div data-region="footer-center"></div>
      <span data-edit="footer.text" data-type="text" data-label="Footer text">© Your company. All rights reserved.</span>
    </div>
    <div class="site-footer-col" data-region="footer-right"></div>
  </div>
</footer>

<style data-tokens>
  :root { --primary: #2563eb; --bg: #ffffff; --text: #172033; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .site-header { width: 100%; border-bottom: 1px solid #e5e7eb; background: var(--bg); padding: 0; }
  .site-header-inner {
    width: 100%; max-width: 1120px; margin: 0 auto; padding: 14px 24px;
    display: grid; grid-template-columns: minmax(0,1fr) auto minmax(0,1fr);
    align-items: center; gap: 16px; min-height: 64px; box-sizing: border-box;
  }
  .site-header-zone { display: flex; align-items: center; gap: 16px; min-width: 0; min-height: 36px; }
  .site-header-zone[data-header-zone="left"] { justify-content: flex-start; }
  .site-header-zone[data-header-zone="center"] { justify-content: center; }
  .site-header-zone[data-header-zone="right"] { justify-content: flex-end; }
  .site-header-brand { display: flex; align-items: center; gap: 10px; }
  .site-brand { font-weight: 700; font-size: 20px; color: var(--text); text-decoration: none; white-space: nowrap; }
  .site-header-logo { display: block; height: 40px; width: auto; max-width: 200px; object-fit: contain; }
  .site-header-brand.hide-name .site-brand,
  .site-header-brand.hide-name [data-edit$=".brand"],
  .site-brand.hide-name { display: none !important; }
  .site-header-brand.has-logo:not(.hide-name) { display: flex; align-items: center; gap: 10px; }
  .site-nav, .site-nav-pages, .site-nav-links {
    display: flex; align-items: center; flex-wrap: wrap; gap: 4px 18px; min-width: 0;
  }
  .site-nav { justify-content: center; }
  .site-nav a, .site-nav-links a { color: inherit; text-decoration: none; font-weight: 600; font-size: 15px; line-height: 1.2; padding: 6px 8px; }
  .site-header [data-instance-id] { padding: 2px 4px; }
  .site-header-actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; }
  .site-header-brand-extra:empty { display: none; }
  .site-header [data-instance-id] { display: inline-flex; align-items: center; }
  .site-header [data-instance-id] > div { max-width: none !important; padding: 0 !important; margin: 0 !important; }
  .site-header-actions [data-edit$=".subtext"]:empty { display: none; }
  .site-main { min-height: 120px; }
  .site-footer { padding: 32px 24px; max-width: 1120px; margin: auto; }
  .site-footer-row {
    display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1.3fr) minmax(0,1fr);
    gap: 16px 24px; align-items: center; width: 100%;
  }
  .site-footer-col { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; min-width: 0; }
  .site-footer-col--center { justify-content: center; flex-direction: column; text-align: center; }
  .site-footer-col > [data-instance-id] > div,
  [data-region^="footer"] > [data-instance-id] > div {
    max-width: none !important; padding-left: 0 !important; padding-right: 0 !important; margin: 0 !important;
  }
</style>
"""


def _warn_ignored_submitted_field_markers(request, html_source, schema):
    ignored = template_svc.ignored_submitted_field_markers(html_source, schema)
    if not ignored:
        return
    marker_names = list(dict.fromkeys(ignored))
    messages.warning(
        request,
        "Saved, but these submitted editable markers were not added to the "
        f"schema: {', '.join(marker_names)}. Check each marker's nearest "
        "data-section and dotted prefix.",
    )


GA_ID_RE = re.compile(r"^(G-[A-Za-z0-9]+|UA-\d+-\d+)$")
SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SESSION_CREDS_KEY = "agency_one_time_creds"
CREDS_TTL_MINUTES = 10


# --------------------------------------------------------------------------- #
# Dispatcher                                                                   #
# --------------------------------------------------------------------------- #


def dashboard_root(request):
    """`/dashboard/` branches on whether the host resolved to a tenant."""
    if request.tenant is not None:
        return tenant_home(request)
    return agency_home(request)


# --------------------------------------------------------------------------- #
# Agency: home / overview                                                      #
# --------------------------------------------------------------------------- #


@agency_operator_required
def agency_home(request):
    sites_qs = Tenant.objects.all()
    total_sites = sites_qs.count()
    published_sites = sites_qs.filter(is_published=True).count()
    draft_sites = total_sites - published_sites

    total_clients = (
        TenantMembership.objects.values("user_id").distinct().count()
    )
    total_templates = Template.objects.count()

    seven_days_ago = timezone.now() - timedelta(days=7)
    sites_edited_recently = (
        Tenant.objects
        .filter(versions__saved_at__gte=seven_days_ago)
        .distinct()
        .count()
    )

    recent_activity = (
        ContentVersion.objects
        .select_related("tenant", "saved_by")
        .order_by("-saved_at")[:10]
    )

    return render(
        request,
        "dashboard/home.html",
        {
            "stats": {
                "total_sites": total_sites,
                "published_sites": published_sites,
                "draft_sites": draft_sites,
                "total_clients": total_clients,
                "total_templates": total_templates,
                "sites_edited_recently": sites_edited_recently,
            },
            "recent_activity": recent_activity,
            "nav_section": "home",
        },
    )


# --------------------------------------------------------------------------- #
# Agency: templates                                                            #
# --------------------------------------------------------------------------- #


@agency_operator_required
def template_list(request):
    templates = (
        Template.objects.all()
        .annotate(tenant_count=Count("tenants"))
        .order_by("-updated_at")
    )
    return render(
        request,
        "dashboard/template_list.html",
        {"templates": templates, "nav_section": "templates"},
    )


@agency_operator_required
def template_create(request):
    if request.method == "POST":
        # "blocks" => start from a blank block shell and attach the block
        # library so the client builds the whole site from our palette.
        # "paste"  => the classic annotated-HTML path.
        build_mode = (request.POST.get("build_mode") or "paste").strip()
        blocks_mode = build_mode == "blocks"
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        html_source = (
            BLOCK_SHELL_STARTER_HTML if blocks_mode
            else (request.POST.get("html_source") or "")
        )

        if not name or (not blocks_mode and not html_source.strip()):
            messages.error(request, "Enter a template name and HTML source.")
            return render(
                request,
                "dashboard/template_form.html",
                {"form_data": request.POST, "html_value": request.POST.get("html_source") or ""},
            )

        if len(name) > 120:
            messages.error(request, "Template name must be 120 characters or fewer.")
            return render(
                request,
                "dashboard/template_form.html",
                {"form_data": request.POST, "html_value": request.POST.get("html_source") or ""},
                status=400,
            )

        with transaction.atomic():
            template = Template.objects.create(
                name=name,
                description=description,
                html_source=html_source,
            )
            if blocks_mode:
                # Block sites are client-editable and get the full palette.
                from core.management.commands.seed_builder_blocks import (
                    seed_block_types,
                )

                template.editing_mode = Template.EDITING_EDITABLE
                template.save(update_fields=["editing_mode", "updated_at"])
                block_types, _created, _updated = seed_block_types()
                template.allowed_block_types.add(*block_types)
            else:
                # Honor the form's Client-editing choice. Blocks mode still
                # forces editable (the palette is the product); a paste with
                # annotated HTML used to ignore "Raw" and always release
                # editing (A7).
                mode = (request.POST.get("editing_mode") or "").strip()
                if mode in {Template.EDITING_RAW, Template.EDITING_EDITABLE}:
                    template.editing_mode = mode
                    template.save(update_fields=["editing_mode", "updated_at"])
                elif template.has_editable_schema:
                    template.editing_mode = Template.EDITING_EDITABLE
                    template.save(update_fields=["editing_mode", "updated_at"])
            template_svc.save_template_version(
                template,
                template.html_source,
                user=request.user,
                label="Initial",
            )

        if blocks_mode:
            messages.success(
                request,
                f"Block site “{template.name}” created with the full block "
                "library. Assign it to a client site, then build pages from the "
                "block palette — no HTML needed.",
            )
        else:
            _warn_ignored_submitted_field_markers(
                request, template.html_source, template.schema
            )
            messages.success(request, f"Template “{template.name}” created.")
        return redirect("dashboard:template_detail", pk=template.pk)

    return render(
        request,
        "dashboard/template_form.html",
        {
            "form_data": {
                "name": "",
                "description": "",
                "html_source": STARTER_TEMPLATE_HTML,
                "editing_mode": Template.EDITING_EDITABLE,
                "build_mode": "blocks",
            },
            "html_value": STARTER_TEMPLATE_HTML,
        },
    )


@agency_operator_required
def template_detail(request, pk):
    template = get_object_or_404(Template, pk=pk)

    if request.method == "POST":
        # Validate BEFORE mutating the instance, so a rejected submit cannot
        # half-apply metadata. A blank field is never "keep the current HTML":
        # that fallback is what turned failed uploads into no-ops that reported
        # success (incident 2026-08-17).
        html_source = request.POST.get("html_source") or ""
        if not html_source.strip():
            messages.error(request, "HTML source cannot be empty.")
            return render(
                request,
                "dashboard/template_form.html",
                {
                    "template": template,
                    "tenants_using": list(
                        template.tenants.only("id", "name", "subdomain").order_by("name")
                    ),
                    # Bound data, so the operator keeps what they typed.
                    "form_data": request.POST,
                    # ...except the HTML, which is what they failed to supply.
                    "html_value": template.html_source,
                },
                status=400,
            )

        template.name = (request.POST.get("name") or template.name).strip()
        template.description = (request.POST.get("description") or "").strip()
        mode = (request.POST.get("editing_mode") or "").strip()
        if mode in {Template.EDITING_RAW, Template.EDITING_EDITABLE}:
            template.editing_mode = mode
        allow_field_loss = request.POST.get("allow_field_loss") in (
            "1",
            "true",
            "on",
            "yes",
        )
        allow_field_drift = request.POST.get("allow_field_drift") in (
            "1",
            "true",
            "on",
            "yes",
        )
        try:
            # One transaction so a single operator submit cannot half-commit:
            # the HTML/version write and the metadata write land together or
            # not at all. save_template_version no longer persists metadata,
            # because it returns early on an unchanged HTML save.
            with transaction.atomic():
                result = template_svc.save_template_version(
                    template,
                    html_source,
                    user=request.user,
                    allow_field_loss=allow_field_loss,
                    allow_field_drift=allow_field_drift,
                )
                template.save(
                    update_fields=["name", "description", "editing_mode", "updated_at"]
                )
        except (template_svc.FieldLossError, template_svc.FieldDriftError) as exc:
            messages.error(request, str(exc))
            # Either error can carry both findings, so render whichever the
            # save is still waiting on rather than one at a time.
            has_loss = bool(getattr(exc, "lost_fields", None))
            has_drift = bool(getattr(exc, "drifted_fields", None))
            tenants_using = list(
                template.tenants.only("id", "name", "subdomain").order_by("name")
            )
            return render(
                request,
                "dashboard/template_form.html",
                {
                    "template": template,
                    "tenants_using": tenants_using,
                    "field_loss": exc if has_loss else None,
                    "field_drift": exc if has_drift else None,
                    # Keep a confirmation the operator already gave, so a
                    # second warning doesn't silently discard the first.
                    "allow_field_loss": allow_field_loss,
                    "allow_field_drift": allow_field_drift,
                    "form_data": {
                        "name": template.name,
                        "description": template.description,
                        "html_source": html_source,
                        "editing_mode": template.editing_mode,
                    },
                    # The operator's candidate, NOT the stored HTML. Handing
                    # back the old value here is what made "save anyway"
                    # re-save the bytes they were replacing.
                    "html_value": html_source,
                },
                status=409,
            )
        if result.unchanged:
            messages.info(request, "No HTML changes; metadata saved.")
        else:
            messages.success(request, "Template updated.")
        _warn_ignored_submitted_field_markers(
            request, html_source, template.schema
        )
        return redirect("dashboard:template_detail", pk=template.pk)

    tenants_using = list(template.tenants.only("id", "name", "subdomain").order_by("name"))
    return render(
        request,
        "dashboard/template_form.html",
        {
            "template": template,
            "tenants_using": tenants_using,
            # form_data is now the single bound source for every field, so the
            # GET seed carries the stored values rather than relying on the
            # template falling back to `template.x` (which silently beat the
            # operator's own input on every error re-render).
            "form_data": {
                "name": template.name,
                "description": template.description,
                "html_source": template.html_source,
                "editing_mode": template.editing_mode,
            },
            "html_value": template.html_source,
        },
    )


@agency_operator_required
@require_POST
def template_delete(request, pk):
    template = get_object_or_404(Template, pk=pk)
    if template.tenants.exists():
        messages.error(
            request,
            "Can't delete a template while sites are using it. "
            "Switch those sites to a different template first.",
        )
        return redirect("dashboard:template_detail", pk=template.pk)
    template.delete()
    messages.success(request, "Template deleted.")
    return redirect("dashboard:template_list")


# --------------------------------------------------------------------------- #
# Agency: embeddable AI assistants                                             #
# --------------------------------------------------------------------------- #


def _assistant_seed_form_data() -> dict:
    return {
        "name": "",
        "slug": "",
        "description": "",
        "brand": "",
        "brand_full": "",
        "greeting": "",
        "suggestions": "",
        "powered_by": "",
        "logo_url": "",
        "orb_logo_url": "",
        "launcher_label": "",
        "voice": "",
        "extra_instructions": "",
        "is_active": "on",
    }


def _assistant_form_context(request, *, assistant=None, form_data=None, status=200):
    form_data = form_data or _assistant_seed_form_data()
    return render(
        request,
        "dashboard/assistant_form.html",
        {
            "assistant_obj": assistant,
            "form_data": form_data,
            "host_origin": settings.EMBED_ASSISTANT_PUBLIC_ORIGIN,
            "nav_section": "assistants",
        },
        status=status,
    )


def _assistant_apply_post(assistant_obj, post_data):
    assistant_obj.name = (post_data.get("name") or "").strip()
    assistant_obj.slug = (post_data.get("slug") or "").strip()
    assistant_obj.description = (post_data.get("description") or "").strip()
    assistant_obj.brand = (post_data.get("brand") or "").strip() or "Assistant"
    assistant_obj.brand_full = (post_data.get("brand_full") or "").strip()
    assistant_obj.greeting = (
        (post_data.get("greeting") or "").strip()
        or "Hi there! How can I help you today?"
    )
    assistant_obj.suggestions = (post_data.get("suggestions") or "").strip()
    assistant_obj.powered_by = (post_data.get("powered_by") or "").strip()
    assistant_obj.logo_url = (post_data.get("logo_url") or "").strip()
    assistant_obj.orb_logo_url = (post_data.get("orb_logo_url") or "").strip()
    assistant_obj.launcher_label = (
        (post_data.get("launcher_label") or "").strip() or "Need help? Ask us!"
    )
    assistant_obj.voice = (post_data.get("voice") or "").strip() or "marin"
    assistant_obj.extra_instructions = (post_data.get("extra_instructions") or "").strip()
    assistant_obj.is_active = post_data.get("is_active") == "on"


@agency_operator_required
def assistant_list(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "all").lower()
    assistants = EmbeddableAssistant.objects.all()
    if q:
        assistants = assistants.filter(
            Q(name__icontains=q)
            | Q(slug__icontains=q)
            | Q(brand__icontains=q)
            | Q(brand_full__icontains=q)
        )
    if status == "active":
        assistants = assistants.filter(is_active=True)
    elif status == "inactive":
        assistants = assistants.filter(is_active=False)

    return render(
        request,
        "dashboard/assistant_list.html",
        {
            "assistants": assistants.order_by("-updated_at"),
            "q": q,
            "status": status,
            "host_origin": settings.EMBED_ASSISTANT_PUBLIC_ORIGIN,
            "nav_section": "assistants",
        },
    )


@agency_operator_required
def assistant_create(request):
    if request.method == "POST":
        form_data = _assistant_seed_form_data() | {k: request.POST.get(k, "") for k in _assistant_seed_form_data().keys()}
        assistant_obj = EmbeddableAssistant()
        _assistant_apply_post(assistant_obj, request.POST)

        if not assistant_obj.name:
            messages.error(request, "Assistant name is required.")
            return _assistant_form_context(
                request, form_data=form_data, status=400
            )
        try:
            assistant_obj.save()
        except Exception as exc:
            messages.error(request, f"Could not create assistant: {exc}")
            return _assistant_form_context(
                request, form_data=form_data, status=400
            )
        messages.success(request, f"Assistant “{assistant_obj.name}” created.")
        return redirect("dashboard:assistant_detail", pk=assistant_obj.pk)

    return _assistant_form_context(request, form_data=_assistant_seed_form_data())


@agency_operator_required
def assistant_detail(request, pk):
    assistant_obj = get_object_or_404(EmbeddableAssistant, pk=pk)
    if request.method == "POST":
        form_data = _assistant_seed_form_data() | {k: request.POST.get(k, "") for k in _assistant_seed_form_data().keys()}
        _assistant_apply_post(assistant_obj, request.POST)
        if not assistant_obj.name:
            messages.error(request, "Assistant name is required.")
            return _assistant_form_context(
                request, assistant=assistant_obj, form_data=form_data, status=400
            )
        try:
            assistant_obj.save()
        except Exception as exc:
            messages.error(request, f"Could not save assistant: {exc}")
            return _assistant_form_context(
                request, assistant=assistant_obj, form_data=form_data, status=400
            )
        messages.success(request, "Assistant updated.")
        return redirect("dashboard:assistant_detail", pk=assistant_obj.pk)

    form_data = {
        "name": assistant_obj.name,
        "slug": assistant_obj.slug,
        "description": assistant_obj.description,
        "brand": assistant_obj.brand,
        "brand_full": assistant_obj.brand_full,
        "greeting": assistant_obj.greeting,
        "suggestions": assistant_obj.suggestions,
        "powered_by": assistant_obj.powered_by,
        "logo_url": assistant_obj.logo_url,
        "orb_logo_url": assistant_obj.orb_logo_url,
        "launcher_label": assistant_obj.launcher_label,
        "voice": assistant_obj.voice,
        "extra_instructions": assistant_obj.extra_instructions,
        "is_active": "on" if assistant_obj.is_active else "",
    }
    return _assistant_form_context(
        request, assistant=assistant_obj, form_data=form_data
    )


@agency_operator_required
@require_POST
def assistant_delete(request, pk):
    assistant_obj = get_object_or_404(EmbeddableAssistant, pk=pk)
    label = assistant_obj.name
    assistant_obj.delete()
    messages.success(request, f"Assistant “{label}” deleted.")
    return redirect("dashboard:assistant_list")


# How long a job may sit non-terminal before the status endpoint declares it
# stale and fails it. The worker thread itself is bounded by OPENAI_TIMEOUT
# (~120s); this only catches a worker that DIED (process restart) and left a row
# stuck "running" forever. Comfortably above the 180s Gunicorn worker budget.
ANNOTATION_JOB_STALE_SECONDS = 300


def _run_annotation_job(job_id, raw_html):
    """Worker body (runs in a background thread, NOT a web request).

    Has no request/proxy timeout, so the OpenAI call may take as long as
    settings.OPENAI_TIMEOUT. Writes the outcome back onto the AnnotationJob row.
    Must never raise out of the thread. Any escape is logged and recorded as an
    error status so the poller stops cleanly instead of hanging forever.
    """
    from django.db import connection

    AnnotationJob.objects.filter(id=job_id).update(status=AnnotationJob.STATUS_RUNNING)
    try:
        annotation = annotate_html_result(raw_html)
        annotated = annotation.html
        schema = build_schema(annotated)
        sections_summary = {
            "items": [
                {
                    "id": s["id"],
                    "label": s["label"],
                    "field_count": len(s.get("fields", [])),
                }
                for s in schema.get("sections", [])
            ],
            "reconciled_fields": annotation.reconciled_fields,
            "dropped_fields": annotation.dropped_fields,
            "backfilled_fields": annotation.backfilled_fields,
            "promoted_sections": annotation.promoted_sections,
            "salvaged_fields": annotation.salvaged_fields,
            "model": annotation.model,
            "prompt_tokens": annotation.prompt_tokens,
            "completion_tokens": annotation.completion_tokens,
            "reasoning_tokens": annotation.reasoning_tokens,
            "total_tokens": annotation.total_tokens,
        }
        AnnotationJob.objects.filter(id=job_id).update(
            status=AnnotationJob.STATUS_DONE,
            result_html=annotated,
            sections=sections_summary,
        )
    except AnnotatorError as exc:
        AnnotationJob.objects.filter(id=job_id).update(
            status=AnnotationJob.STATUS_ERROR, error=str(exc)
        )
    except Exception as exc:  # noqa: BLE001; background thread must never crash silently
        logger.exception("Annotation job %s crashed", job_id)
        AnnotationJob.objects.filter(id=job_id).update(
            status=AnnotationJob.STATUS_ERROR,
            error=f"Unexpected error during annotation: {exc}",
        )
    finally:
        # Threads get their own DB connection; close it so it isn't leaked.
        connection.close()


@agency_operator_required
@require_POST
def template_fetch_url(request):
    """Fetch an HTML page from a URL so the operator can pre-fill the template
    form's HTML textarea without copy-pasting. The fetched HTML then goes
    through the existing AI annotator like any pasted source.

    On responses that look like an unrendered SPA shell (Vite/React/Next/etc.
    deploys whose index.html is just a ``<div id="root">`` mount point), the
    view re-fetches through a headless browser so the JS bundle runs and we
    capture the hydrated DOM, then inlines external stylesheets and
    absolutizes asset URLs so the resulting HTML is self-contained.

    Expects JSON body: ``{"url": "https://example.com/", "force_render": false}``
    Returns: ``{"html": "...", "rendered_with_js": bool, "warning": "..."}``
    on success, ``{"error": "..."}`` on failure.
    """
    from core.services.url_fetch import (
        UrlFetchError,
        fetch_url_html,
        inline_external_assets,
        looks_like_spa_shell,
        render_url_html,
    )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    url = (payload.get("url") or "").strip()
    if not url:
        return JsonResponse({"error": "URL is required."}, status=400)
    force_render = bool(payload.get("force_render"))

    rendered_with_js = False
    warning: str | None = None

    if force_render:
        # Operator explicitly asked for JS rendering; skip the static fetch.
        try:
            html = render_url_html(url)
            html = inline_external_assets(html, base_url=url)
            rendered_with_js = True
        except UrlFetchError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    else:
        try:
            html = fetch_url_html(url)
        except UrlFetchError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        if looks_like_spa_shell(html):
            try:
                rendered = render_url_html(url)
                html = inline_external_assets(rendered, base_url=url)
                rendered_with_js = True
            except UrlFetchError as exc:
                # Render path unavailable / failed; keep the static fetch
                # and tell the operator what happened so they can either
                # install the dependency on the server or paste manually.
                warning = (
                    "This URL looks like a JavaScript-rendered single-page "
                    "app (the static response has almost no content). "
                    f"Couldn't auto-render it: {exc}"
                )

    response: dict[str, object] = {
        "html": html,
        "bytes": len(html),
        "rendered_with_js": rendered_with_js,
    }
    if warning:
        response["warning"] = warning
    return JsonResponse(response)


@agency_operator_required
@require_POST
def template_annotate(request):
    """Kick off a background AI annotation job and return its id immediately.

    The slow OpenAI call runs in a worker thread (see _run_annotation_job), so
    this request returns in milliseconds and can never be killed by the Gunicorn
    worker timeout / proxy. The browser polls template_annotate_status.
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    raw_html = (payload.get("html") or "").strip()
    if not raw_html:
        return JsonResponse({"error": "No HTML provided."}, status=400)

    # Opportunistic sweep of transient rows so result_html blobs don't accumulate.
    AnnotationJob.objects.filter(
        created_at__lt=timezone.now() - timedelta(days=1)
    ).delete()

    job = AnnotationJob.objects.create(
        created_by=request.user if request.user.is_authenticated else None,
    )
    threading.Thread(
        target=_run_annotation_job,
        args=(str(job.id), raw_html),
        name=f"annotate-{job.id}",
        daemon=True,
    ).start()

    return JsonResponse({"job_id": str(job.id), "status": job.status}, status=202)


@agency_operator_required
@require_GET
def template_annotate_status(request, job_id):
    """Poll a background annotation job. Returns its status, and on completion
    the annotated HTML + section summary (mirrors the old synchronous payload)."""
    try:
        job = AnnotationJob.objects.get(id=job_id)
    except AnnotationJob.DoesNotExist:
        return JsonResponse({"error": "Job not found."}, status=404)

    # Scope to the creator so one operator can't poll another's job (superusers
    # see everything for debugging).
    if (
        job.created_by_id
        and job.created_by_id != request.user.id
        and not request.user.is_superuser
    ):
        return JsonResponse({"error": "Job not found."}, status=404)

    # Stale guard: a job stuck non-terminal well past the worker budget means the
    # worker thread died (e.g. the process was recycled). Fail it so the UI can
    # offer a retry instead of polling forever.
    if not job.is_terminal:
        age = (timezone.now() - job.updated_at).total_seconds()
        if age > ANNOTATION_JOB_STALE_SECONDS:
            AnnotationJob.objects.filter(
                id=job.id,
                status__in=[AnnotationJob.STATUS_PENDING, AnnotationJob.STATUS_RUNNING],
            ).update(
                status=AnnotationJob.STATUS_ERROR,
                error="Annotation timed out on the server. Please try again.",
            )
            job.refresh_from_db()

    body = {"job_id": str(job.id), "status": job.status}
    if job.status == AnnotationJob.STATUS_DONE:
        body["html"] = job.result_html
        if isinstance(job.sections, dict):
            body["sections"] = job.sections.get("items", [])
            for key in (
                "reconciled_fields",
                "dropped_fields",
                "backfilled_fields",
                "promoted_sections",
                "salvaged_fields",
                "model",
                "prompt_tokens",
                "completion_tokens",
                "reasoning_tokens",
                "total_tokens",
            ):
                body[key] = job.sections.get(key, 0)
        else:
            # Rows created before integrity counters used a plain section list.
            body["sections"] = job.sections
    elif job.status == AnnotationJob.STATUS_ERROR:
        body["error"] = job.error or "Annotation failed."
    return JsonResponse(body)


# --------------------------------------------------------------------------- #
# Agency: sites list                                                           #
# --------------------------------------------------------------------------- #


@agency_operator_required
def tenant_list(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "all").lower()

    tenants = (
        Tenant.objects.all()
        .select_related("template")
        .annotate(
            member_count=Count("memberships", distinct=True),
            last_edited=Max("versions__saved_at"),
        )
        .order_by("-updated_at")
    )

    if q:
        tenants = tenants.filter(Q(name__icontains=q) | Q(subdomain__icontains=q))
    if status == "published":
        tenants = tenants.filter(is_published=True)
    elif status == "draft":
        tenants = tenants.filter(is_published=False)

    tenants = list(tenants)
    for tenant in tenants:
        tenant.public_url = tenant_public_url(request, tenant)

    return render(
        request,
        "dashboard/tenant_list.html",
        {
            "tenants": tenants,
            "q": q,
            "status": status,
            "nav_section": "sites",
        },
    )


# --------------------------------------------------------------------------- #
# Agency: new client flow                                                      #
# --------------------------------------------------------------------------- #


@agency_operator_required
def tenant_create(request):
    """One-screen new client flow: User + Tenant + Membership atomically.

    Also supports creating a new Template inline by selecting `__new__`
    in the template dropdown. The Template is created inside the same
    transaction so a partial failure leaks nothing.
    """
    templates = _templates_available()

    form_data = {
        "name": "",
        "subdomain": "",
        # Default to the inline new-template flow. Operators almost always
        # build a fresh site per client (paste URL → Fetch → Annotate); the
        # saved-template dropdown is collapsed behind a disclosure link.
        "template": "__new__",
        "custom_domain": "",
        "client_username": "",
        "client_email": "",
        "new_template_name": "",
        "new_template_description": "",
        "new_template_html": "",
    }

    if request.method != "POST":
        form_data["new_template_html"] = STARTER_TEMPLATE_HTML
        return render(
            request,
            "dashboard/tenant_form.html",
            {
                "templates": templates,
                "form_data": form_data,
                "nav_section": "sites",
            },
        )

    name = (request.POST.get("name") or "").strip()
    submitted_subdomain = (request.POST.get("subdomain") or "").strip().lower()
    subdomain = submitted_subdomain
    template_id = request.POST.get("template") or ""
    custom_domain = (request.POST.get("custom_domain") or "").strip()
    client_username = (request.POST.get("client_username") or "").strip()
    client_email = (request.POST.get("client_email") or "").strip()
    new_template_name = (request.POST.get("new_template_name") or "").strip()
    new_template_description = (request.POST.get("new_template_description") or "").strip()
    new_template_html = request.POST.get("new_template_html") or ""

    if not subdomain and name:
        subdomain = _generate_unique_subdomain_from_name(name)

    posted = {
        "name": name,
        "subdomain": submitted_subdomain,
        "template": template_id,
        "custom_domain": custom_domain,
        "client_username": client_username,
        "client_email": client_email,
        "new_template_name": new_template_name,
        "new_template_description": new_template_description,
        "new_template_html": new_template_html,
    }

    errors = []
    inline_new_template = template_id == "__new__"
    # "Build with blocks": create a blank block-shell template + attach the full
    # block library, so the client builds the whole site from the palette. No
    # HTML paste, no annotation.
    blocks_new_template = template_id == "__blocks__"
    make_new_template = inline_new_template or blocks_new_template

    if not name:
        errors.append("Site name is required.")
    elif len(name) > 120:
        errors.append("Site name must be 120 characters or fewer.")

    sub_reason = _validate_subdomain(subdomain) if subdomain else None
    if sub_reason:
        errors.append({
            "invalid": "Subdomain must use lowercase letters, digits, and dashes only.",
            "reserved": f"“{subdomain}” is a reserved subdomain. Pick another.",
            "taken": f"“{subdomain}” is already taken. Pick another.",
        }[sub_reason])

    template = None
    if not template_id:
        errors.append("Pick a template.")
    elif inline_new_template:
        # Inline path is plug-and-forget; template name is optional and
        # falls back to the site name. The slug is auto-uniqued in
        # Template.save() so reusing the site name across clients is safe.
        if not new_template_name:
            new_template_name = name or "Site template"
        if not new_template_html.strip():
            errors.append("New template HTML is required.")
    elif blocks_new_template:
        # Blank block site — no HTML required; the shell + library make it fully
        # client-buildable. Name falls back to the site name.
        if not new_template_name:
            new_template_name = name or "Site template"
        new_template_html = BLOCK_SHELL_STARTER_HTML
    else:
        try:
            template = Template.objects.get(pk=template_id)
        except (Template.DoesNotExist, ValueError):
            errors.append("That template no longer exists.")

    if not client_username:
        errors.append("Client username is required.")
    elif len(client_username) > 150:
        errors.append("Client username must be 150 characters or fewer.")
    elif User.objects.filter(username__iexact=client_username).exists():
        errors.append(
            f"A user named “{client_username}” already exists. "
            "Pick a different username, or add the existing user from the site detail page."
        )

    if errors:
        for e in errors:
            messages.error(request, e)
        return render(
            request,
            "dashboard/tenant_form.html",
            {
                "templates": templates,
                "form_data": posted,
                "nav_section": "sites",
            },
            status=400,
        )

    try:
        # Palette attach lives in the same outer transaction as tenant create
        # so a seed failure cannot leave a login with an empty palette (A12).
        with transaction.atomic():
            tenant, user, password = create_tenant_account(
                name=name,
                subdomain=subdomain,
                custom_domain=custom_domain,
                template=template,
                username=client_username,
                email=client_email,
                new_template=(
                    {
                        "name": new_template_name,
                        "description": new_template_description,
                        "html_source": new_template_html,
                    }
                    if make_new_template
                    else None
                ),
            )
            if blocks_new_template:
                from core.management.commands.seed_builder_blocks import (
                    seed_block_types,
                )

                block_types, _created, _updated = seed_block_types()
                tenant.template.allowed_block_types.add(*block_types)
    except template_svc.CrossTenantTemplateError as exc:
        messages.error(request, str(exc))
        return render(
            request,
            "dashboard/tenant_form.html",
            {
                "templates": templates,
                "form_data": posted,
                "nav_section": "sites",
            },
            status=400,
        )
    except Exception as exc:
        messages.error(
            request,
            f"Couldn't create the site: {exc}. Nothing was saved.",
        )
        return render(
            request,
            "dashboard/tenant_form.html",
            {
                "templates": templates,
                "form_data": posted,
                "nav_section": "sites",
            },
            status=400,
        )

    if inline_new_template:
        _warn_ignored_submitted_field_markers(
            request, new_template_html, tenant.template.schema
        )
    token = _stash_credentials_in_session(request, user, password)
    return redirect(
        f"{reverse('dashboard:site_created', args=[tenant.pk])}?token={token}"
    )


@agency_operator_required
@require_GET
def check_subdomain(request):
    """JSON endpoint: GET /dashboard/sites/check-subdomain/?value=..."""
    # Lowercase first, exactly like the submit path (tenant_create) does, so the
    # live AJAX check and the actual creation agree — "MySite" is available, not
    # "invalid" (A13).
    value = (request.GET.get("value") or "").strip().lower()
    if not value:
        return JsonResponse({"available": False, "reason": "invalid"})
    reason = _validate_subdomain(value)
    if reason:
        return JsonResponse({"available": False, "reason": reason})
    return JsonResponse({"available": True})


def _validate_subdomain(value):
    """Return None if available, otherwise a reason code."""
    if not value or not SUBDOMAIN_RE.match(value):
        return "invalid"
    reserved = set(getattr(settings, "TENANT_RESERVED_SUBDOMAINS", set()))
    if value in reserved:
        return "reserved"
    if Tenant.objects.filter(subdomain=value).exists():
        return "taken"
    return None


def _generate_unique_subdomain_from_name(name):
    """
    Build a valid, available subdomain from a site name.

    Starts with slugified name and appends numeric suffixes on collisions.
    """
    max_len = 63
    base = slugify(name or "").strip("-")
    if not base:
        base = "site"
    base = base[:max_len].rstrip("-") or "site"

    candidate = base
    suffix = 1
    while _validate_subdomain(candidate) is not None:
        token = f"-{suffix}"
        stem = base[: max_len - len(token)].rstrip("-") or "site"
        candidate = f"{stem}{token}"
        suffix += 1
    return candidate


# --------------------------------------------------------------------------- #
# Agency: credentials (one-time view)                                          #
# --------------------------------------------------------------------------- #


def _stash_credentials_in_session(request, user, password):
    token = secrets.token_urlsafe(24)
    bucket = request.session.get(SESSION_CREDS_KEY) or {}
    bucket[token] = {
        "user_id": user.pk,
        "username": user.username,
        "password": password,
        "expires_at": (
            timezone.now() + timedelta(minutes=CREDS_TTL_MINUTES)
        ).isoformat(),
    }
    request.session[SESSION_CREDS_KEY] = bucket
    request.session.modified = True
    return token


def _pop_credentials_from_session(request, token, *, allowed_user_ids=None):
    """Return the credentials dict and remove it. Returns None if missing/expired.

    ``allowed_user_ids`` (when given) binds the token to a specific user (or set
    of users): a token minted for one user can't be revealed on a different
    user's / site's credentials page even if the operator lands there with the
    token in the URL (A9)."""
    bucket = request.session.get(SESSION_CREDS_KEY) or {}
    payload = bucket.pop(token, None)
    if payload is None:
        return None
    request.session[SESSION_CREDS_KEY] = bucket
    request.session.modified = True
    try:
        expires_at = datetime.fromisoformat(payload["expires_at"])
    except (KeyError, ValueError):
        return None
    if timezone.now() > expires_at:
        return None
    if allowed_user_ids is not None and payload.get("user_id") not in set(allowed_user_ids):
        return None
    return payload


@agency_operator_required
@require_GET
def site_created(request, pk):
    """
    Post-create / post-reveal landing for a tenant site.

    Merges the one-time credentials reveal with the shareable URL bundle
    so the operator gets everything in one shot. Without a fresh `?token=`,
    the credentials block is omitted and only the URL panel renders. The
    page is then safe to bookmark/refresh.
    """
    tenant = get_object_or_404(Tenant, pk=pk)
    token = request.GET.get("token") or ""
    # Credentials shown here belong to the site's owner or one of its members
    # (member-add also lands here). Bind the token to that set so a token minted
    # for another user can't be revealed on this site's page (A9).
    allowed_ids = {tenant.owner_id} | set(
        tenant.memberships.values_list("user_id", flat=True)
    )
    payload = (
        _pop_credentials_from_session(request, token, allowed_user_ids=allowed_ids)
        if token else None
    )
    urls = build_tenant_url_bundle(request, tenant)

    return render(
        request,
        "dashboard/site_created.html",
        {
            "tenant": tenant,
            "payload": payload,
            "urls": urls,
            "agency_editor_url": reverse("dashboard:tenant_editor", args=[tenant.pk]),
            "tenant_detail_url": reverse("dashboard:tenant_detail", args=[tenant.pk]),
            "back_url": reverse("dashboard:tenant_list"),
            "user_detail_url": (
                reverse("dashboard:user_detail", args=[payload["user_id"]])
                if payload else None
            ),
            "nav_section": "sites",
        },
    )


# Back-compat alias: /sites/<pk>/credentials/ now lands on the same page.
site_credentials = site_created


@agency_operator_required
@require_GET
def user_credentials(request, pk):
    user = get_object_or_404(User, pk=pk)
    token = request.GET.get("token") or ""
    # The token must have been minted for this exact user (A9).
    payload = (
        _pop_credentials_from_session(request, token, allowed_user_ids={user.pk})
        if token else None
    )

    return render(
        request,
        "dashboard/credentials.html",
        {
            "context_label": "user",
            "credentials_user": user,
            "payload": payload,
            "login_url": None,
            "back_url": reverse("dashboard:user_detail", args=[user.pk]),
            "back_label": "Done: back to user",
            "user_detail_url": reverse("dashboard:user_detail", args=[user.pk]),
        },
    )


# --------------------------------------------------------------------------- #
# Agency: site detail / membership management                                  #
# --------------------------------------------------------------------------- #


@agency_operator_required
def tenant_detail(request, pk):
    tenant = get_object_or_404(
        Tenant.objects.select_related("template", "owner"), pk=pk
    )
    members = (
        tenant.memberships
        .select_related("user")
        .order_by("user__username")
    )
    member_user_ids = list(members.values_list("user_id", flat=True))
    add_member_candidates = (
        User.objects.exclude(pk__in=member_user_ids)
        .filter(is_active=True)
        .order_by("username")[:200]
    )
    activity = (
        tenant.versions.select_related("saved_by").order_by("-saved_at")[:20]
    )
    available_templates = _templates_available(tenant=tenant)
    bound = set(
        GhlInstall.objects.exclude(tenant__isnull=True).values_list("location_id", flat=True)
    )
    connectable = []
    for agency in GhlAgencyInstall.objects.all():
        for loc in agency.available_locations:
            loc_id = loc.get("id")
            if not loc_id or loc_id in bound:
                continue
            connectable.append({"agency_id": agency.pk, "id": loc_id, "name": loc.get("name", "")})
    page_rows = [
        {"obj": page, "urls": _page_row_urls(request, "agency", tenant, page)}
        for page in tenant.pages.select_related("template").all()
    ]
    return render(
        request,
        "dashboard/tenant_detail.html",
        {
            "tenant": tenant,
            "members": members,
            "add_member_candidates": add_member_candidates,
            "activity": activity,
            # The custom-domain panel is rendered here on initial page load and
            # re-rendered via fetch-swap by _render_custom_domain_partial. Both
            # paths go through _custom_domain_context so the domain list, DNS
            # record names, and target_ip stay identical (no first-load blanks).
            **_custom_domain_context(tenant),
            "nav_section": "sites",
            "role_choices": TenantMembership.ROLE_CHOICES,
            "available_templates": available_templates,
            # URLs for visiting the client's live site (subdomain host) and a
            # fallback that always works on the current host (/site/<sub>/).
            "site_urls": build_tenant_url_bundle(request, tenant),
            "connectable_subaccounts": connectable,
            "pages": page_rows,
            "pages_manage_url": reverse("dashboard:page_list", args=[tenant.pk]),
        },
    )


@agency_operator_required
@require_POST
def tenant_settings_update(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    name = (request.POST.get("name") or "").strip()
    new_subdomain = (request.POST.get("subdomain") or "").strip().lower()

    if not name:
        messages.error(request, "Site name is required.")
        return redirect("dashboard:tenant_detail", pk=tenant.pk)
    if len(name) > 120:
        messages.error(request, "Site name must be 120 characters or fewer.")
        return redirect("dashboard:tenant_detail", pk=tenant.pk)

    if new_subdomain != tenant.subdomain:
        reason = _validate_subdomain(new_subdomain)
        if reason == "taken":
            # Check it's not just our own row.
            other = Tenant.objects.filter(subdomain=new_subdomain).exclude(pk=tenant.pk).exists()
            if other:
                messages.error(request, f"“{new_subdomain}” is already taken.")
                return redirect("dashboard:tenant_detail", pk=tenant.pk)
        elif reason:
            messages.error(
                request,
                {
                    "invalid": "Subdomain must use lowercase letters, digits, and dashes.",
                    "reserved": f"“{new_subdomain}” is a reserved subdomain.",
                }[reason],
            )
            return redirect("dashboard:tenant_detail", pk=tenant.pk)
        tenant.subdomain = new_subdomain

    tenant.name = name

    ghl_location_id = (request.POST.get("ghl_location_id") or "").strip() or None
    if ghl_location_id != tenant.ghl_location_id:
        if ghl_location_id and Tenant.objects.filter(ghl_location_id=ghl_location_id).exclude(pk=tenant.pk).exists():
            messages.error(request, f"GHL location ID “{ghl_location_id}” is already linked to another site.")
            return redirect("dashboard:tenant_detail", pk=tenant.pk)
        tenant.ghl_location_id = ghl_location_id

    tenant.save(update_fields=["name", "subdomain", "ghl_location_id", "updated_at"])
    messages.success(request, "Site settings updated.")
    return redirect("dashboard:tenant_detail", pk=tenant.pk)


@agency_operator_required
@require_POST
def tenant_template_swap(request, pk):
    """Re-point ``Tenant.template`` at a different ``Template`` row.

    The tenant's ``content`` JSON is left intact: fields that exist under
    the same ``section.field`` id in the new template's schema keep
    rendering with the saved value; fields that don't have a slot in the
    new schema sit dormant on the row and come back if the agency ever
    swaps back. Nothing is deleted.
    """
    tenant = get_object_or_404(Tenant.objects.select_related("template"), pk=pk)
    raw = (request.POST.get("template_id") or "").strip()
    try:
        new_template = Template.objects.get(pk=int(raw))
    except (Template.DoesNotExist, ValueError, TypeError):
        messages.error(request, "Pick a valid template.")
        return redirect("dashboard:tenant_detail", pk=tenant.pk)

    if new_template.pk == tenant.template_id:
        messages.info(request, f"“{tenant.name}” already uses that template.")
        return redirect("dashboard:tenant_detail", pk=tenant.pk)

    old_template_name = tenant.template.name if tenant.template_id else "None"
    try:
        assigned = template_svc.assign_template(
            tenant, new_template, user=request.user
        )
    except template_svc.CrossTenantTemplateError as exc:
        messages.error(request, str(exc))
        return redirect("dashboard:tenant_detail", pk=tenant.pk)

    messages.success(
        request,
        f"Switched “{tenant.name}” from “{old_template_name}” to "
        f"“{assigned.name}”. Existing content survives where field IDs "
        "match the new template; the rest stays on the row and comes back "
        "if you switch back."
    )
    return redirect("dashboard:tenant_detail", pk=tenant.pk)


@agency_operator_required
@require_POST
def tenant_delete(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    confirm = (request.POST.get("confirm_subdomain") or "").strip().lower()
    if confirm != tenant.subdomain.lower():
        messages.error(
            request,
            "To delete, you must type the site's subdomain exactly.",
        )
        return redirect("dashboard:tenant_detail", pk=tenant.pk)
    site_name = tenant.name
    try:
        tenant.delete()
    except ProtectedError:
        logger.exception(
            "tenant_delete blocked by ProtectedError (tenant_id=%s subdomain=%s)",
            tenant.pk,
            tenant.subdomain,
        )
        messages.error(
            request,
            "Could not delete this site because another record still references "
            "one of its templates. Remove or reassign those pages/templates "
            "first, then try again.",
        )
        return redirect("dashboard:tenant_detail", pk=tenant.pk)
    except IntegrityError:
        logger.exception(
            "tenant_delete blocked by IntegrityError (tenant_id=%s subdomain=%s)",
            tenant.pk,
            tenant.subdomain,
        )
        messages.error(
            request,
            "Could not delete this site due to a template slug conflict when "
            "returning its templates to the library. Contact an engineer if "
            "this keeps happening.",
        )
        return redirect("dashboard:tenant_detail", pk=tenant.pk)
    messages.success(request, f"Site “{site_name}” deleted.")
    return redirect("dashboard:tenant_list")


@agency_operator_required
@require_POST
def tenant_member_add(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    user_id = request.POST.get("user_id") or ""
    role = request.POST.get("role") or TenantMembership.ROLE_EDITOR
    if role not in dict(TenantMembership.ROLE_CHOICES):
        role = TenantMembership.ROLE_EDITOR
    try:
        user = User.objects.get(pk=user_id)
    except (User.DoesNotExist, ValueError):
        messages.error(request, "That user no longer exists.")
        return redirect("dashboard:tenant_detail", pk=tenant.pk)
    obj, created = TenantMembership.objects.get_or_create(
        tenant=tenant, user=user, defaults={"role": role}
    )
    if created:
        messages.success(request, f"Added {user.username} as {obj.get_role_display()}.")
    else:
        messages.info(request, f"{user.username} is already a member.")
    return redirect("dashboard:tenant_detail", pk=tenant.pk)


@agency_operator_required
@require_POST
def tenant_member_create(request, pk):
    """Mint a brand-new login for an *existing* site (no new site created).

    Unlike tenant_member_add (which attaches an already-existing user), this
    creates the User + Membership in one shot and reveals one-time credentials.
    """
    tenant = get_object_or_404(Tenant, pk=pk)
    user, password, errors = create_scoped_login(
        tenant,
        username=request.POST.get("username"),
        email=request.POST.get("email"),
        role=request.POST.get("role") or TenantMembership.ROLE_EDITOR,
    )
    if errors:
        for e in errors:
            messages.error(request, e)
        return redirect(f"{reverse('dashboard:tenant_detail', args=[tenant.pk])}#members")

    token = _stash_credentials_in_session(request, user, password)
    messages.success(request, f"Login created for {user.username}.")
    return redirect(
        f"{reverse('dashboard:site_credentials', args=[tenant.pk])}?token={token}"
    )


@agency_operator_required
@require_POST
def tenant_member_remove(request, pk, membership_id):
    tenant = get_object_or_404(Tenant, pk=pk)
    membership = get_object_or_404(
        TenantMembership, pk=membership_id, tenant=tenant
    )
    # The owner's own membership is load-bearing (Tenant.owner is required and
    # now PROTECTed). Removing it would leave the site with an owner who has no
    # membership row — confusing and inconsistent with the Team self-serve copy.
    if membership.user_id == tenant.owner_id:
        messages.error(
            request,
            "You can't remove the site owner. Transfer ownership or delete the "
            "site instead.",
        )
        return redirect("dashboard:tenant_detail", pk=tenant.pk)
    username = membership.user.username
    membership.delete()
    messages.success(request, f"Removed {username} from this site.")
    return redirect("dashboard:tenant_detail", pk=tenant.pk)


@agency_operator_required
@require_POST
def tenant_member_role(request, pk, membership_id):
    tenant = get_object_or_404(Tenant, pk=pk)
    membership = get_object_or_404(
        TenantMembership, pk=membership_id, tenant=tenant
    )
    role = request.POST.get("role") or membership.role
    if role not in dict(TenantMembership.ROLE_CHOICES):
        messages.error(request, "Unknown role.")
        return redirect("dashboard:tenant_detail", pk=tenant.pk)
    membership.role = role
    membership.save(update_fields=["role"])
    messages.success(request, f"Updated role for {membership.user.username}.")
    return redirect("dashboard:tenant_detail", pk=tenant.pk)


# --------------------------------------------------------------------------- #
# Agency: user management                                                      #
# --------------------------------------------------------------------------- #


@agency_operator_required
def user_list(request):
    q = (request.GET.get("q") or "").strip()
    role = (request.GET.get("role") or "all").lower()

    users = (
        User.objects.all()
        .annotate(membership_count=Count("tenant_memberships", distinct=True))
        .order_by("username")
    )
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q))
    if role == "staff":
        users = users.filter(Q(is_staff=True) | Q(is_superuser=True))
    elif role == "client":
        users = users.filter(is_staff=False, is_superuser=False)

    user_rows = []
    for u in users:
        memberships = list(
            TenantMembership.objects.select_related("tenant")
            .filter(user=u)
            .order_by("tenant__name")
        )
        site_names = [m.tenant.name for m in memberships]
        user_rows.append({
            "user": u,
            "site_names": site_names,
            "site_names_truncated": site_names[:3],
            "site_names_overflow": max(0, len(site_names) - 3),
            "membership_count": len(memberships),
        })

    return render(
        request,
        "dashboard/user_list.html",
        {
            "user_rows": user_rows,
            "q": q,
            "role": role,
            "nav_section": "users",
        },
    )


@agency_operator_required
def user_detail(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    memberships = (
        TenantMembership.objects.filter(user=user_obj)
        .select_related("tenant")
        .order_by("tenant__name")
    )
    return render(
        request,
        "dashboard/user_detail.html",
        {
            "user_obj": user_obj,
            "memberships": memberships,
            "role_choices": TenantMembership.ROLE_CHOICES,
            "nav_section": "users",
        },
    )


@agency_operator_required
@require_POST
def user_create_login(request, pk):
    """From a client's user page, mint an *additional* login on one of this
    client's own sites, i.e. create a new user on their behalf.

    Scoped to sites the client already belongs to, so this can't be used to
    attach accounts to arbitrary tenants from a user page.
    """
    user_obj = get_object_or_404(User, pk=pk)
    tenant = (
        Tenant.objects.filter(pk=request.POST.get("tenant_id") or "", memberships__user=user_obj)
        .first()
    )
    if tenant is None:
        messages.error(request, "Pick one of this client's sites.")
        return redirect("dashboard:user_detail", pk=user_obj.pk)

    new_user, password, errors = create_scoped_login(
        tenant,
        username=request.POST.get("username"),
        email=request.POST.get("email"),
        role=request.POST.get("role") or TenantMembership.ROLE_EDITOR,
    )
    if errors:
        for e in errors:
            messages.error(request, e)
        return redirect("dashboard:user_detail", pk=user_obj.pk)

    token = _stash_credentials_in_session(request, new_user, password)
    messages.success(request, f"Login created for {new_user.username} on {tenant.name}.")
    return redirect(
        f"{reverse('dashboard:site_credentials', args=[tenant.pk])}?token={token}"
    )


def _may_target_user(actor, target) -> bool:
    """A superuser can be acted on (reset/deactivate/activate) only by another
    superuser. Non-superuser staff manage clients, not each other's admins (A4)."""
    return (not target.is_superuser) or actor.is_superuser


def _flush_user_sessions(user) -> int:
    """Delete every active session belonging to ``user`` so a password reset or
    deactivation logs them out everywhere (C4). Django stores the user id inside
    the encoded session, so we decode candidates and match. Fine at current
    scale; revisit with a session-key index if the table grows large."""
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    uid = str(user.pk)
    killed = 0
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if session.get_decoded().get("_auth_user_id") == uid:
            session.delete()
            killed += 1
    return killed


@agency_operator_required
@require_POST
def user_reset_password(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    if not _may_target_user(request.user, user_obj):
        return HttpResponseForbidden("Only a superuser can reset another superuser.")
    password = generate_password()
    user_obj.set_password(password)
    user_obj.save(update_fields=["password"])
    # Reset invalidates the old credential everywhere — kill live sessions so a
    # compromised session can't outlive the reset (C4).
    _flush_user_sessions(user_obj)
    token = _stash_credentials_in_session(request, user_obj, password)
    return redirect(
        f"{reverse('dashboard:user_credentials', args=[user_obj.pk])}?token={token}"
    )


@agency_operator_required
@require_POST
def user_deactivate(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    if user_obj.pk == request.user.pk:
        messages.error(request, "You can't deactivate your own account.")
        return redirect("dashboard:user_detail", pk=user_obj.pk)
    if not _may_target_user(request.user, user_obj):
        return HttpResponseForbidden("Only a superuser can deactivate another superuser.")
    user_obj.is_active = False
    user_obj.save(update_fields=["is_active"])
    # Deactivation should take effect immediately, not at natural expiry (C4).
    _flush_user_sessions(user_obj)
    messages.success(request, f"Deactivated {user_obj.username}.")
    return redirect("dashboard:user_detail", pk=user_obj.pk)


@agency_operator_required
@require_POST
def user_activate(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    if not _may_target_user(request.user, user_obj):
        return HttpResponseForbidden("Only a superuser can activate another superuser.")
    user_obj.is_active = True
    user_obj.save(update_fields=["is_active"])
    messages.success(request, f"Activated {user_obj.username}.")
    return redirect("dashboard:user_detail", pk=user_obj.pk)


@agency_operator_required
@require_POST
def user_make_staff(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Only a superuser can promote agency staff.")
    user_obj = get_object_or_404(User, pk=pk)
    user_obj.is_staff = True
    user_obj.save(update_fields=["is_staff"])
    messages.success(request, f"{user_obj.username} is now agency staff.")
    return redirect("dashboard:user_detail", pk=user_obj.pk)


@agency_operator_required
@require_POST
def user_remove_membership(request, pk, membership_id):
    user_obj = get_object_or_404(User, pk=pk)
    membership = get_object_or_404(
        TenantMembership, pk=membership_id, user=user_obj
    )
    # Same rule as tenant_member_remove: never strip the owner's own membership.
    if membership.user_id == membership.tenant.owner_id:
        messages.error(
            request,
            "You can't remove a site owner from their own site. Transfer "
            "ownership or delete the site instead.",
        )
        return redirect("dashboard:user_detail", pk=user_obj.pk)
    tenant_name = membership.tenant.name
    membership.delete()
    messages.success(
        request, f"Removed {user_obj.username} from “{tenant_name}”."
    )
    return redirect("dashboard:user_detail", pk=user_obj.pk)


# --------------------------------------------------------------------------- #
# Agency-side editor (unchanged from previous spec)                            #
# --------------------------------------------------------------------------- #


@agency_operator_required
def tenant_editor(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _render_editor(request, tenant, scope="agency")


@agency_operator_required
def tenant_preview(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _render_preview(tenant)


@agency_operator_required
@require_POST
def tenant_save(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _save_content(request, tenant)


@agency_operator_required
@require_GET
def tenant_ghl_forms(request, pk):
    return _ghl_forms_json(get_object_or_404(Tenant, pk=pk))


@agency_operator_required
@require_POST
def tenant_publish(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _toggle_publish(
        request, tenant,
        redirect_url=reverse("dashboard:tenant_editor", args=[tenant.pk]),
    )


@agency_operator_required
@require_POST
def tenant_upload(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _save_upload(request, tenant)


@agency_operator_required
@require_POST
def tenant_video_upload(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _save_video_upload(request, tenant)


@agency_operator_required
@require_GET
def tenant_media_gallery(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _media_gallery_list(tenant)


@agency_operator_required
@require_http_methods(["POST", "DELETE"])
def tenant_media_item(request, pk, asset_id):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _media_item_mutate(request, tenant, asset_id)


# --------------------------------------------------------------------------- #
# Tenant surface (tenant resolved on host, member or staff only)               #
# --------------------------------------------------------------------------- #


@tenant_member_required
def tenant_home(request):
    return _render_editor(request, request.tenant, scope="tenant")


@tenant_member_required
def tenant_preview_self(request):
    return _render_preview(request.tenant)


@tenant_member_required
@require_POST
def tenant_save_self(request):
    return _save_content(request, request.tenant)


@tenant_member_required
@require_GET
def tenant_ghl_forms_self(request):
    return _ghl_forms_json(request.tenant)


@tenant_member_required
@require_POST
def tenant_publish_self(request):
    return _toggle_publish(
        request, request.tenant,
        redirect_url=reverse("dashboard:tenant_home"),
    )


@tenant_member_required
@require_POST
def tenant_upload_self(request):
    return _save_upload(request, request.tenant)


@tenant_member_required
@require_POST
def tenant_video_upload_self(request):
    return _save_video_upload(request, request.tenant)


@tenant_member_required
@require_GET
def tenant_media_gallery_self(request):
    return _media_gallery_list(request.tenant)


@tenant_member_required
@require_http_methods(["POST", "DELETE"])
def tenant_media_item_self(request, asset_id):
    return _media_item_mutate(request, request.tenant, asset_id)


# --------------------------------------------------------------------------- #
# Tenant surface: Team (self-serve logins for this site)                       #
# --------------------------------------------------------------------------- #


@tenant_member_required
@require_GET
def team_self(request):
    """Client-facing member management for the current tenant.

    Any member (owner or editor) can create additional logins scoped to this
    site. All created accounts are non-staff and reach only this tenant.
    """
    tenant = request.tenant
    members = tenant.memberships.select_related("user").order_by("user__username")
    return render(
        request,
        "dashboard/team.html",
        {
            "tenant": tenant,
            "members": members,
            "role_choices": TenantMembership.ROLE_CHOICES,
            "owner_id": tenant.owner_id,
            "current_user_id": request.user.id,
            "team_create_url": reverse("dashboard:team_member_create_self"),
        },
    )


@tenant_member_required
@require_POST
def team_member_create_self(request):
    user, password, errors = create_scoped_login(
        request.tenant,
        username=request.POST.get("username"),
        email=request.POST.get("email"),
        role=request.POST.get("role") or TenantMembership.ROLE_EDITOR,
    )
    if errors:
        for e in errors:
            messages.error(request, e)
        return redirect("dashboard:team_self")

    token = _stash_credentials_in_session(request, user, password)
    messages.success(request, f"Login created for {user.username}.")
    return redirect(f"{reverse('dashboard:team_credentials_self')}?token={token}")


@tenant_member_required
@require_GET
def team_credentials_self(request):
    """One-time credential reveal for a client-created login."""
    token = request.GET.get("token") or ""
    # Bind to this tenant's owner/members so a stray token can't reveal another
    # site's credentials here (A9).
    allowed_ids = {request.tenant.owner_id} | set(
        request.tenant.memberships.values_list("user_id", flat=True)
    )
    payload = (
        _pop_credentials_from_session(request, token, allowed_user_ids=allowed_ids)
        if token else None
    )
    return render(
        request,
        "dashboard/credentials.html",
        {
            "context_label": "login",
            "credentials_user": None,
            "payload": payload,
            "tenant": request.tenant,
            "login_url": f"{request.scheme}://{request.get_host()}{reverse('login')}",
            "back_url": reverse("dashboard:team_self"),
            "back_label": "Done: back to Team",
            "user_detail_url": None,
        },
    )


@tenant_member_required
@require_POST
def team_member_remove_self(request, membership_id):
    tenant = request.tenant
    membership = get_object_or_404(
        TenantMembership, pk=membership_id, tenant=tenant
    )
    if membership.user_id == request.user.id:
        messages.error(request, "You can't remove your own access.")
        return redirect("dashboard:team_self")
    if membership.user_id == tenant.owner_id:
        messages.error(request, "The site owner can't be removed here. Ask your agency.")
        return redirect("dashboard:team_self")
    username = membership.user.username
    membership.delete()
    messages.success(request, f"Removed {username} from this site.")
    return redirect("dashboard:team_self")


@tenant_member_required
@require_POST
def team_member_role_self(request, membership_id):
    tenant = request.tenant
    membership = get_object_or_404(
        TenantMembership, pk=membership_id, tenant=tenant
    )
    role = request.POST.get("role") or membership.role
    if role not in dict(TenantMembership.ROLE_CHOICES):
        messages.error(request, "Unknown role.")
        return redirect("dashboard:team_self")
    if membership.user_id == tenant.owner_id and role != TenantMembership.ROLE_OWNER:
        messages.error(request, "The site owner's role can't be changed here. Ask your agency.")
        return redirect("dashboard:team_self")
    membership.role = role
    membership.save(update_fields=["role"])
    messages.success(request, f"Updated role for {membership.user.username}.")
    return redirect("dashboard:team_self")


# --------------------------------------------------------------------------- #
# Inner pages (additional annotated pages: /about/, /services/, ...)           #
# --------------------------------------------------------------------------- #


def _get_tenant_page(tenant, page_pk):
    return get_object_or_404(Page, pk=page_pk, tenant=tenant)


def _page_nav_urls(scope, tenant):
    if scope == "tenant":
        return {
            "list": reverse("dashboard:page_list_self"),
            "new": reverse("dashboard:page_create_self"),
            "nav_order": reverse("dashboard:page_nav_reorder_self"),
            "home": reverse("dashboard:tenant_home"),
            "blog": reverse("dashboard:blog_list_self"),
        }
    return {
        "list": reverse("dashboard:page_list", args=[tenant.pk]),
        "new": reverse("dashboard:page_create", args=[tenant.pk]),
        "nav_order": reverse("dashboard:page_nav_reorder", args=[tenant.pk]),
        "home": reverse("dashboard:tenant_editor", args=[tenant.pk]),
        "blog": reverse("dashboard:blog_list", args=[tenant.pk]),
    }


def _page_row_urls(request, scope, tenant, page):
    if scope == "tenant":
        return {
            "edit": reverse("dashboard:page_editor_self", args=[page.pk]),
            "publish": reverse("dashboard:page_publish_self", args=[page.pk]),
            "delete": reverse("dashboard:page_delete_self", args=[page.pk]),
            "rename": reverse("dashboard:page_rename_self", args=[page.pk]),
            # Client is already on the tenant host, so a relative slug link stays there.
            "live": f"/{page.slug}/",
        }
    urls = {
        "edit": reverse("dashboard:page_editor", args=[tenant.pk, page.pk]),
        "publish": reverse("dashboard:page_publish", args=[tenant.pk, page.pk]),
        "delete": reverse("dashboard:page_delete", args=[tenant.pk, page.pk]),
        "rename": reverse("dashboard:page_rename", args=[tenant.pk]),
        # Agency host: link to the client's canonical tenant host, not the apex
        # `/site/<sub>/` fallback, so the page opens on <sub>.<base>/<slug>/.
        "live": f"{tenant_public_url(request, tenant)}{page.slug}/",
    }
    # Shared-shell pages reuse the site template — Edit HTML would rewrite
    # home + every sibling. Only offer the action when this page owns its
    # own template (classic paste-HTML pages) (A1).
    if page.template_id and page.template_id != tenant.template_id:
        urls["edit_html"] = reverse(
            "dashboard:page_edit_html", args=[tenant.pk, page.pk]
        )
    return urls


def _user_can_manage_pages(request):
    """Page create / rename / delete / nav-order.

    Agency staff always may. Tenant members (clients) may too when their site
    runs on a block *shell* — the Phase-2 relaxation of the locked-structure
    promise. Clients compose pages from the curated block palette on the shared
    shell, so they still cannot author raw HTML or invent layout; they only
    stack agency-designed sections. Sites still on a classic (non-shell)
    template stay agency-managed, because adding a client page there would need
    pasted HTML. Caps are enforced at create time.
    """
    if request.user.is_staff or request.user.is_superuser:
        return True
    tenant = getattr(request, "tenant", None)
    tpl = getattr(tenant, "template", None)
    return bool(tpl and tpl.is_block_shell)


def _page_list(request, tenant, scope):
    from core.services import blocks as _blocks

    _blocks.ensure_block_editor(tenant, user=request.user)
    tenant.refresh_from_db()

    can_manage = _user_can_manage_pages(request)
    pages = [
        {"obj": p, "urls": _page_row_urls(request, scope, tenant, p)}
        for p in tenant.pages.all()
    ]
    # A block shell lets clients compose pages from the palette (no HTML paste).
    # The template shows the simplified "New page" card when this is true.
    is_shell = bool(tenant.template and tenant.template.is_block_shell)
    return render(
        request,
        "dashboard/page_list.html",
        {
            "tenant": tenant,
            "scope": scope,
            "pages": pages,
            "nav_urls": _page_nav_urls(scope, tenant),
            "can_manage_pages": can_manage,
            "is_block_shell": is_shell,
            "page_cap": _blocks.MAX_PAGES_PER_TENANT,
            "at_page_cap": tenant.pages.count() >= _blocks.MAX_PAGES_PER_TENANT,
            # Don't leak the agency template catalog to clients.
            "templates": (
                _templates_available(tenant=tenant) if can_manage else []
            ),
            "reserved_slugs": ", ".join(sorted(RESERVED_PAGE_SLUGS)),
            "nav_section": "pages" if scope == "tenant" else "sites",
        },
    )


def _page_create(request, tenant, scope):
    from core.services import blocks as _blocks

    _blocks.ensure_block_editor(tenant, user=request.user)
    tenant.refresh_from_db()
    nav = _page_nav_urls(scope, tenant)
    title = (request.POST.get("title") or "").strip()
    slug = slugify(request.POST.get("slug") or title)[:80]
    html_source = request.POST.get("html_source") or ""

    errors = []
    if not title:
        errors.append("A page title is required.")
    elif len(title) > 120:
        errors.append("Page title must be 120 characters or fewer.")
    if not slug:
        errors.append("A URL slug is required.")
    elif slug in RESERVED_PAGE_SLUGS:
        errors.append(f"'/{slug}/' is reserved. Choose a different slug.")
    elif tenant.pages.filter(slug=slug).exists():
        errors.append(f"This site already has a page at /{slug}/.")
    if not html_source.strip():
        errors.append("Paste the page HTML.")
    from core.services import blocks as _blocks
    if tenant.pages.count() >= _blocks.MAX_PAGES_PER_TENANT:
        errors.append(
            f"This site has reached the maximum of {_blocks.MAX_PAGES_PER_TENANT} pages."
        )

    if errors:
        for e in errors:
            messages.error(request, e)
        return redirect(nav["list"])

    # Each page owns its OWN template, built from the pasted HTML, so editing one
    # page's HTML can never affect another page or the home site.
    with transaction.atomic():
        template = Template.objects.create(
            name=f"{tenant.name}: {title}",
            html_source=html_source,
            tenant=tenant,
        )
        if template.has_editable_schema:
            template.editing_mode = Template.EDITING_EDITABLE
            template.save(update_fields=["editing_mode", "updated_at"])
        template_svc.save_template_version(
            template, template.html_source, user=request.user, label="Initial"
        )
        page = Page.objects.create(tenant=tenant, template=template, title=title, slug=slug)
    _warn_ignored_submitted_field_markers(
        request, html_source, template.schema
    )
    messages.success(request, f"Page “{page.title}” created. Start editing.")
    if scope == "tenant":
        return redirect("dashboard:page_editor_self", page_pk=page.pk)
    return redirect("dashboard:page_editor", pk=tenant.pk, page_pk=page.pk)


def _page_create_shared(request, tenant, scope):
    """Client-safe page creation — no HTML paste.

    The new page shares the site's block *shell* template and starts empty (or
    copies the home page's blocks), so a client composes it entirely from the
    curated palette. This is what lets us relax "clients can't add pages"
    without also relaxing "clients can't author HTML / invent layout".
    """
    from django.db.models import Max

    from core.services import blocks as _blocks

    nav = _page_nav_urls(scope, tenant)
    shell = tenant.template
    if not (shell and shell.is_block_shell):
        messages.error(
            request,
            "Your agency hasn't enabled add-your-own pages for this site yet.",
        )
        return redirect(nav["list"])
    if tenant.pages.count() >= _blocks.MAX_PAGES_PER_TENANT:
        messages.error(
            request,
            f"This site has reached the maximum of {_blocks.MAX_PAGES_PER_TENANT} pages.",
        )
        return redirect(nav["list"])

    title = (request.POST.get("title") or "").strip()
    slug = slugify(request.POST.get("slug") or title)[:80]
    start_from = request.POST.get("start_from") or "blank"

    errors = []
    if not title:
        errors.append("A page title is required.")
    elif len(title) > 120:
        errors.append("Page title must be 120 characters or fewer.")
    if not slug:
        errors.append("A URL slug is required.")
    elif slug in RESERVED_PAGE_SLUGS:
        errors.append(f"'/{slug}/' is reserved — choose a different slug.")
    elif tenant.pages.filter(slug=slug).exists():
        errors.append(f"This site already has a page at /{slug}/.")
    if errors:
        for e in errors:
            messages.error(request, e)
        return redirect(nav["list"])

    # "Start from" can copy the block set from the home page or any existing
    # page (cross-page reuse), always with fresh instance ids so the pages stay
    # independent. `start_from` is "blank" | "copy_home" | "copy_page:<pk>".
    source_regions = {}
    if start_from == "copy_home":
        source_regions = (tenant.content or {}).get("regions") or {}
    elif start_from.startswith("copy_page:"):
        try:
            src = tenant.pages.get(pk=int(start_from.split(":", 1)[1]))
            source_regions = (src.content or {}).get("regions") or {}
        except (ValueError, Page.DoesNotExist):
            source_regions = {}
    # Deep-clone each source block (recursing into column children) with fresh
    # ids so a home page built from layout rows copies its nested content too,
    # and the two pages stay independent (C2). Header/footer extras copy too.
    copied = {}
    for slot, items in (source_regions or {}).items():
        cloned_list = [
            cloned
            for cloned in (_blocks.clone_instance_tree(b) for b in (items or []))
            if cloned is not None
        ]
        copied[slot] = cloned_list
    if "main" not in copied:
        copied["main"] = []
    content = {"regions": copied}

    next_order = (tenant.pages.aggregate(m=Max("nav_order"))["m"] or 0) + 1
    page = Page.objects.create(
        tenant=tenant, template=shell, title=title, slug=slug,
        content=content, nav_order=next_order,
    )
    messages.success(request, f"Page “{page.title}” created — start adding sections.")
    if scope == "tenant":
        return redirect("dashboard:page_editor_self", page_pk=page.pk)
    return redirect("dashboard:page_editor", pk=tenant.pk, page_pk=page.pk)


def _page_rename(request, tenant, scope, page_pk):
    page = _get_tenant_page(tenant, page_pk)
    nav = _page_nav_urls(scope, tenant)
    title = (request.POST.get("title") or "").strip()
    slug = slugify(request.POST.get("slug") or title)[:80]

    errors = []
    if not title:
        errors.append("A page title is required.")
    elif len(title) > 120:
        errors.append("Page title must be 120 characters or fewer.")
    if not slug:
        errors.append("A URL slug is required.")
    elif slug in RESERVED_PAGE_SLUGS:
        errors.append(f"'/{slug}/' is reserved — choose a different slug.")
    elif tenant.pages.filter(slug=slug).exclude(pk=page.pk).exists():
        errors.append(f"This site already has a page at /{slug}/.")
    if errors:
        for e in errors:
            messages.error(request, e)
        return redirect(nav["list"])

    page.title = title
    page.slug = slug
    page.save(update_fields=["title", "slug", "updated_at"])
    messages.success(request, f"Page renamed to “{page.title}”.")
    return redirect(nav["list"])


def _page_nav_reorder(request, tenant, scope):
    """Persist menu order + visibility from the Pages UI. Body (JSON):
    ``{"order": [{"id": 3, "show_in_nav": true}, ...]}`` — array position is the
    new ``nav_order``."""
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    order = payload.get("order")
    if not isinstance(order, list):
        return JsonResponse({"ok": False, "error": "order must be a list"}, status=400)

    pages = {p.pk: p for p in tenant.pages.all()}
    for i, item in enumerate(order):
        if not isinstance(item, dict):
            continue
        page = pages.get(item.get("id"))
        if page is None:
            continue
        page.nav_order = i
        if "show_in_nav" in item:
            page.show_in_nav = bool(item.get("show_in_nav"))
        page.save(update_fields=["nav_order", "show_in_nav"])
    return JsonResponse({"ok": True})


def _page_delete(request, tenant, scope, page_pk):
    page = _get_tenant_page(tenant, page_pk)
    title = page.title
    page.delete()
    messages.success(request, f"Page “{title}” deleted.")
    return redirect(_page_nav_urls(scope, tenant)["list"])


# ----- Inner pages: agency surface ----------------------------------------- #


@agency_operator_required
def page_list(request, pk):
    return _page_list(request, get_object_or_404(Tenant, pk=pk), "agency")


@agency_operator_required
@require_POST
def page_create(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    # Block-shell sites compose pages from the palette (no HTML). Staff can
    # still paste raw HTML for a classic per-page template by submitting
    # html_source (the import flow and non-shell sites use that path).
    if not request.POST.get("html_source") and tenant.template and tenant.template.is_block_shell:
        return _page_create_shared(request, tenant, "agency")
    return _page_create(request, tenant, "agency")


@agency_operator_required
@require_POST
def page_rename(request, pk):
    raw = request.POST.get("page_pk")
    try:
        page_pk = int(raw)
    except (TypeError, ValueError):
        messages.error(request, "Which page are you renaming?")
        return redirect("dashboard:page_list", pk=pk)
    return _page_rename(
        request, get_object_or_404(Tenant, pk=pk), "agency", page_pk,
    )


@agency_operator_required
@require_POST
def page_nav_reorder(request, pk):
    return _page_nav_reorder(request, get_object_or_404(Tenant, pk=pk), "agency")


def _annotate_template_in_background(
    template_id: int,
    raw_html: str,
    job_id: str | None = None,
) -> None:
    """Run the AI annotator on `raw_html` and update Template `template_id` in
    place when it completes. Used by page_import_siblings to upgrade an
    imported sibling Page from "renders as static HTML" to "editable in CMS"
    asynchronously. The import response returns immediately, and the
    annotated HTML lands a minute or two later.

    A persisted AnnotationJob makes completion or failure visible to the
    importing operator. The raw Template remains usable if annotation fails.
    """
    from django.db import connection

    def update_job(**values):
        if job_id:
            AnnotationJob.objects.filter(id=job_id).update(**values)

    update_job(status=AnnotationJob.STATUS_RUNNING)
    try:
        # annotate_html returns the annotated HTML as a STRING.
        annotated_html = annotate_html(raw_html)
    except AnnotatorError as exc:
        logger.warning(
            "Sibling annotation failed for template=%s: %s", template_id, exc,
        )
        update_job(status=AnnotationJob.STATUS_ERROR, error=str(exc))
        connection.close()
        return
    except Exception as exc:
        logger.exception(
            "Sibling annotation crashed for template=%s: %s", template_id, exc,
        )
        update_job(
            status=AnnotationJob.STATUS_ERROR,
            error=f"Unexpected error during sibling annotation: {exc}",
        )
        connection.close()
        return
    try:
        template = Template.objects.get(pk=template_id)
        # If the operator already saved a newer HTML (or another process
        # wrote the row), do not clobber it with the original fetch (A8).
        if (template.html_source or "") != (raw_html or ""):
            logger.info(
                "Sibling annotation skipped for template=%s: HTML changed "
                "since import.",
                template_id,
            )
            update_job(
                status=AnnotationJob.STATUS_DONE,
                result_html=template.html_source,
                sections={"items": [], "reconciled_fields": 0, "skipped": "html_changed"},
            )
            connection.close()
            return
        # Annotation usually adds fields; allow loss so a partial model
        # rewrite cannot leave the row stuck mid-import.
        result = template_svc.save_template_version(
            template,
            annotated_html,
            user=None,
            allow_field_loss=True,
            label="AI annotation",
        )
        if result.unchanged:
            # No version to cut, but the promotion below still has to run: an
            # already-annotated template can be sitting at editing_mode=raw.
            logger.info(
                "Sibling annotation returned unchanged HTML for template=%s",
                template_id,
            )
        if template.has_editable_schema:
            template.editing_mode = Template.EDITING_EDITABLE
            template.save(update_fields=["editing_mode", "updated_at"])
        section_count = len((template.schema or {}).get("sections", []))
        logger.info(
            "Sibling annotation applied to template=%s (%d sections)",
            template_id, section_count,
        )
        update_job(
            status=AnnotationJob.STATUS_DONE,
            result_html=annotated_html,
            sections={
                "items": [
                    {
                        "id": section["id"],
                        "label": section["label"],
                        "field_count": len(section.get("fields", [])),
                    }
                    for section in (template.schema or {}).get("sections", [])
                ],
                "reconciled_fields": 0,
                "dropped_fields": 0,
                "backfilled_fields": 0,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Could not save sibling annotation for template=%s", template_id,
        )
        update_job(
            status=AnnotationJob.STATUS_ERROR,
            error=f"Could not save sibling annotation: {exc}",
        )
    finally:
        connection.close()


@agency_operator_required
@require_POST
def page_import_siblings(request, pk):
    """Import every same-origin .html sibling found on a source URL as a Page.

    Operator pastes the home URL of the client's original deploy
    (e.g. https://susan-rabbyv2.pages.dev/). We fetch the home, scan for
    same-origin .html links (privacy, terms, about, etc.), and for each
    discovered URL: fetch the page, rewrite relative links the same way
    fetch_url_html does, create a Template with the rewritten HTML, and
    bind it to this Tenant via a Page row.

    Pages are created and returned IMMEDIATELY with the raw fetched HTML
    so the operator can navigate to them right away. They render as
    static HTML at the right CMS URLs. Then for each sibling we spawn a
    worker thread that runs the AI annotator and patches the Template's
    html_source in place when annotation finishes (~30–120 s per page),
    promoting the Page from static-only to editable-in-CMS without
    blocking the import request.

    Body (JSON): {"home_url": "https://...source.example.com/"}
    Returns: {"created": [{"slug": "...", "title": "...", "page_id": ...,
                          "annotation_status": "pending"}, ...],
              "skipped": [{"slug": "...", "reason": "..."}, ...]}
    """
    from core.services.url_fetch import (
        UrlFetchError,
        discover_sibling_html_urls,
        fetch_url_html,
    )

    tenant = get_object_or_404(Tenant, pk=pk)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    home_url = (payload.get("home_url") or "").strip()
    if not home_url:
        return JsonResponse({"error": "home_url is required."}, status=400)

    try:
        # We need the RAW home HTML (no rewrites) to discover unrewritten
        # relative links. Once siblings are discovered, each sibling is
        # fetched with rewrites enabled so its own internal links land on
        # the correct origins.
        home_html = fetch_url_html(home_url, rewrite_urls=False)
    except UrlFetchError as exc:
        return JsonResponse(
            {"error": f"Could not fetch the home URL: {exc}"}, status=400,
        )

    siblings = discover_sibling_html_urls(home_html, home_url)
    if not siblings:
        return JsonResponse({
            "created": [],
            "skipped": [],
            "message": "No same-origin .html siblings found on that page.",
        })

    created: list[dict] = []
    skipped: list[dict] = []
    from core.services import blocks as _blocks
    pages_left = max(0, _blocks.MAX_PAGES_PER_TENANT - tenant.pages.count())

    for sibling in siblings:
        slug = sibling["slug"][:80]
        title = sibling["title"][:120]

        if slug in RESERVED_PAGE_SLUGS:
            skipped.append({"slug": slug, "reason": "reserved slug"})
            continue
        if tenant.pages.filter(slug=slug).exists():
            skipped.append({"slug": slug, "reason": "page already exists"})
            continue
        if pages_left <= 0:
            skipped.append({"slug": slug, "reason": "page cap reached"})
            continue

        try:
            sibling_html = fetch_url_html(sibling["url"], rewrite_urls=True)
        except UrlFetchError as exc:
            skipped.append({"slug": slug, "reason": f"fetch failed: {exc}"})
            continue

        with transaction.atomic():
            template = Template.objects.create(
                name=f"{tenant.name}: {title}",
                description=f"Imported from {sibling['url']}",
                html_source=sibling_html,
                tenant=tenant,
            )
            template_svc.save_template_version(
                template,
                template.html_source,
                user=request.user,
                label="Import",
            )
            page = Page.objects.create(
                tenant=tenant, template=template, title=title, slug=slug,
            )

        annotation_job = AnnotationJob.objects.create(created_by=request.user)
        threading.Thread(
            target=_annotate_template_in_background,
            args=(template.pk, sibling_html, str(annotation_job.id)),
            name=f"annotate-sibling-{template.pk}",
            daemon=True,
        ).start()

        created.append({
            "slug": slug, "title": title, "page_id": page.pk,
            "annotation_status": "pending",
            "annotation_job_id": str(annotation_job.id),
            "annotation_status_url": reverse(
                "dashboard:template_annotate_status", args=[annotation_job.id]
            ),
        })
        pages_left -= 1

    return JsonResponse({"created": created, "skipped": skipped})


@agency_operator_required
def page_editor(request, pk, page_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    page = _get_tenant_page(tenant, page_pk)
    return _render_editor(request, tenant, scope="agency", page=page)


@agency_operator_required
def page_edit_html(request, pk, page_pk):
    """Edit a page's raw HTML (agency-only, structural). Each page owns its own
    template, so this edits in place safely. GET shows the current HTML in the
    same paste + annotate editor as templates; POST saves it and rebuilds the
    schema so new fields appear in the content editor immediately."""
    tenant = get_object_or_404(Tenant, pk=pk)
    page = _get_tenant_page(tenant, page_pk)
    # Shared-shell pages reuse Tenant.template. Saving HTML here would rewrite
    # home + every sibling. Operators edit the site template instead (A1).
    if page.template_id and tenant.template_id and page.template_id == tenant.template_id:
        messages.error(
            request,
            f"“{page.title}” shares the site shell. Edit the site template "
            f"to change header/footer HTML — this action would affect every page.",
        )
        return redirect("dashboard:template_detail", pk=tenant.template.pk)
    if request.method == "POST":
        new_html = request.POST.get("html_source") or ""
        if not new_html.strip():
            messages.error(request, "Page HTML cannot be empty.")
        else:
            allow_field_loss = request.POST.get("allow_field_loss") in (
                "1", "true", "on", "yes",
            )
            allow_field_drift = request.POST.get("allow_field_drift") in (
                "1", "true", "on", "yes",
            )
            try:
                result = template_svc.save_template_version(
                    page.template,
                    new_html,
                    user=request.user,
                    allow_field_loss=allow_field_loss,
                    allow_field_drift=allow_field_drift,
                )
            except (
                template_svc.FieldLossError,
                template_svc.FieldDriftError,
            ) as exc:
                messages.error(request, str(exc))
                return render(
                    request,
                    "dashboard/page_edit_html.html",
                    {
                        "tenant": tenant,
                        "page": page,
                        "html_source": new_html,
                        "field_loss": exc if getattr(exc, "lost_fields", None) else None,
                        "field_drift": (
                            exc if getattr(exc, "drifted_fields", None) else None
                        ),
                        "allow_field_loss": allow_field_loss,
                        "allow_field_drift": allow_field_drift,
                        "save_url": reverse(
                            "dashboard:page_edit_html", args=[tenant.pk, page.pk]
                        ),
                        "back_url": reverse("dashboard:page_list", args=[tenant.pk]),
                    },
                    status=409,
                )
            if result.unchanged:
                messages.info(request, f"No HTML changes for “{page.title}”.")
            else:
                messages.success(request, f"HTML updated for “{page.title}”.")
            _warn_ignored_submitted_field_markers(
                request, new_html, page.template.schema
            )
            return redirect("dashboard:page_editor", pk=tenant.pk, page_pk=page.pk)
    return render(
        request,
        "dashboard/page_edit_html.html",
        {
            "tenant": tenant,
            "page": page,
            "html_source": page.template.html_source,
            "save_url": reverse("dashboard:page_edit_html", args=[tenant.pk, page.pk]),
            "back_url": reverse("dashboard:page_list", args=[tenant.pk]),
        },
    )


@agency_operator_required
def page_preview(request, pk, page_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _render_preview(_get_tenant_page(tenant, page_pk))


@agency_operator_required
@require_POST
def page_save(request, pk, page_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _save_content(request, _get_tenant_page(tenant, page_pk))


@agency_operator_required
@require_POST
def page_publish(request, pk, page_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    page = _get_tenant_page(tenant, page_pk)
    return _toggle_publish(
        request, page, noun="Page",
        redirect_url=reverse("dashboard:page_editor", args=[tenant.pk, page.pk]),
    )


@agency_operator_required
@require_POST
def page_delete(request, pk, page_pk):
    return _page_delete(request, get_object_or_404(Tenant, pk=pk), "agency", page_pk)


# ----- Inner pages: tenant surface (self) ----------------------------------- #


@tenant_member_required
def page_list_self(request):
    return _page_list(request, request.tenant, "tenant")


@tenant_member_required
@require_POST
def page_create_self(request):
    from core.services import blocks as _blocks

    _blocks.ensure_block_editor(request.tenant, user=request.user)
    request.tenant.refresh_from_db()
    if not _user_can_manage_pages(request):
        messages.error(request, "Adding pages is managed by your agency. Get in touch and they'll set it up.")
        return redirect("dashboard:page_list_self")
    # Staff may still paste raw HTML; clients always go through the curated
    # shared-shell flow (no HTML, blocks only).
    if request.POST.get("html_source") and (request.user.is_staff or request.user.is_superuser):
        return _page_create(request, request.tenant, "tenant")
    return _page_create_shared(request, request.tenant, "tenant")


@tenant_member_required
@require_POST
def page_rename_self(request, page_pk):
    if not _user_can_manage_pages(request):
        messages.error(request, "Renaming pages is managed by your agency.")
        return redirect("dashboard:page_list_self")
    return _page_rename(request, request.tenant, "tenant", page_pk)


@tenant_member_required
@require_POST
def page_nav_reorder_self(request):
    if not _user_can_manage_pages(request):
        return JsonResponse({"ok": False, "error": "not allowed"}, status=403)
    return _page_nav_reorder(request, request.tenant, "tenant")


@tenant_member_required
def page_editor_self(request, page_pk):
    return _render_editor(
        request, request.tenant, scope="tenant",
        page=_get_tenant_page(request.tenant, page_pk),
    )


@tenant_member_required
def page_preview_self(request, page_pk):
    return _render_preview(_get_tenant_page(request.tenant, page_pk))


@tenant_member_required
@require_POST
def page_save_self(request, page_pk):
    return _save_content(request, _get_tenant_page(request.tenant, page_pk))


@tenant_member_required
@require_POST
def page_publish_self(request, page_pk):
    page = _get_tenant_page(request.tenant, page_pk)
    return _toggle_publish(
        request, page, noun="Page",
        redirect_url=reverse("dashboard:page_editor_self", args=[page.pk]),
    )


@tenant_member_required
@require_POST
def page_delete_self(request, page_pk):
    # Structural change: clients can't remove pages; only agency staff can.
    if not _user_can_manage_pages(request):
        messages.error(request, "Removing pages is managed by your agency.")
        return redirect("dashboard:page_list_self")
    return _page_delete(request, request.tenant, "tenant", page_pk)


# --------------------------------------------------------------------------- #
# Shared helpers                                                               #
# --------------------------------------------------------------------------- #


def _render_editor(request, tenant, *, scope, page=None):
    # The editor drives either the tenant home (page=None) or one inner Page.
    # Both expose the same template / content / is_published shape, so the only
    # differences are the action URLs and the bar labels.
    editable = page or tenant
    from core.services import blocks as _blocks

    _blocks.ensure_block_editor(editable, user=request.user)
    if page is None:
        tenant.refresh_from_db()
        editable = tenant
    else:
        page.refresh_from_db()
        editable = page
    # Build the schema fresh from the template HTML so the editor always reflects
    # the CURRENT parser (per-element style flags, theme tokens, etc.) even for
    # templates whose stored schema predates those features (avoids a stale
    # schema hiding the Style panels / Design tab until every template is
    # re-saved). Public rendering still uses the stored schema for defaults.
    tpl = editable.template
    if tpl and tpl.html_source:
        schema = build_schema(tpl.html_source)
    else:
        schema = (tpl.schema if tpl else None) or {"sections": []}
    content = merge_with_defaults(schema, editable.content)
    theme_tokens = schema.get("theme_tokens", [])

    sections = schema.get("sections", [])
    # Brand tokens (global colors) and the header navigation are conceptually
    # distinct from per-section content edits, so the editor surfaces each on its
    # own tab ("Brand" / "Navigation"). Everything else is "Content".
    brand_section = next((s for s in sections if s.get("id") == "brand"), None)
    # The "Navigation" tab gathers the site chrome, both the header nav and the
    # footer, so they live apart from the page-body content sections.
    nav_groups = {"header", "footer", "global"}
    chrome_ids = {"nav", "footer", "header"}
    nav_sections = [
        s for s in sections
        if s.get("id") != "brand" and (
            (s.get("group") or "").lower() in nav_groups
            or (s.get("id") or "").lower() in chrome_ids
        )
    ]
    nav_ids = {s.get("id") for s in nav_sections}
    # Within the Navigation tab, split into Header / Footer sub-tabs.
    footer_sections = [
        s for s in nav_sections
        if (s.get("id") or "").lower() == "footer"
        or (s.get("group") or "").lower() == "footer"
    ]
    header_sections = [s for s in nav_sections if s not in footer_sections]
    content_sections = [
        s for s in sections
        if s.get("id") != "brand" and s.get("id") not in nav_ids
    ]

    # Block-instance mode: when the template is a shell (has a data-region
    # slot), the Content tab is driven by the ordered block INSTANCES the client
    # placed, not the shell's own (chrome-only) sections. Each instance becomes
    # a form "section" whose field ids are the per-instance `instanceId.field`,
    # so the existing field.html + editor.js binding works unchanged.
    block_mode = bool(tpl and tpl.is_block_shell)
    palette: list = []
    block_defaults: dict = {}
    region_name = "main"
    if block_mode:
        from core.services import blocks as _blocks

        palette = _blocks.palette_for_template(tpl)
        catalog = _blocks.catalog_for_template(tpl)
        block_defaults = {
            key: (entry["schema"].get("defaults") or {})
            for key, entry in catalog.items()
        }
        regions = (editable.content or {}).get("regions") or {}
        shell_regions = _blocks.shell_region_names(tpl.html_source)
        instance_sections: list = []
        _region_group = {
            "header": "Header", "header-left": "Header", "header-center": "Header",
            "header-right": "Header", "nav": "Header",
            "footer": "Footer", "footer-left": "Footer", "footer-center": "Footer",
            "footer-right": "Footer",
        }

        def _walk(inst_list, depth, parent_id, region, group):
            # Depth-first so a block dropped into a column appears right after
            # its row in the form list. Each nested instance still binds via
            # `<instanceId>.<field>`, so field.html + editor.js work unchanged.
            for inst in inst_list or []:
                entry = catalog.get(inst.get("type"))
                if not entry:
                    continue
                fields = []
                for field in entry["schema"].get("fields", []):
                    fentry = dict(field)
                    fentry["id"] = f"{inst['id']}.{field['id']}"
                    fields.append(fentry)
                slot_names = entry.get("regions") or []
                instance_sections.append({
                    "id": inst["id"],
                    "label": entry.get("label") or inst.get("type"),
                    "icon": entry.get("icon") or "square",
                    "group": group,
                    "fields": fields,
                    "is_instance": True,
                    "block_type": inst.get("type"),
                    "depth": depth,
                    "parent_id": parent_id,
                    "region": region,
                    "is_layout": bool(slot_names),
                })
                children = inst.get("children") or {}
                for name in slot_names:
                    _walk(children.get(name) or [], depth + 1, inst["id"], name, group)

        for slot in shell_regions:
            if slot in _blocks._HEADER_SLOTS or slot.startswith("header"):
                continue
            _walk(
                regions.get(slot) or [],
                0,
                None,
                slot,
                _region_group.get(slot, "Sections"),
            )
        content_sections = instance_sections
        _blocks.ensure_header(content)

    grouped: dict[str, list] = {}
    for section in content_sections:
        grouped.setdefault(section.get("group", "Sections"), []).append(section)

    # Layout mode is driven by how many entries land in the section nav.
    # Block pages keep the Layers sidebar even when the canvas is empty —
    # compact (sidebar hidden) is for classic locked templates only.
    if block_mode:
        layout_mode = "standard" if len(content_sections) <= 15 else "dense"
    else:
        layout_mode = "compact" if len(content_sections) <= 6 else (
            "standard" if len(content_sections) <= 15 else "dense"
        )

    # Image/video uploads create per-tenant MediaAssets (page-independent), so
    # the page editor reuses the tenant-scoped upload/video endpoints. Version
    # history is home-only for now; pages pass empty version URLs and the
    # editor hides the History button (see editor.html).
    if scope == "tenant":
        ghl_forms_url = reverse("dashboard:tenant_ghl_forms_self")
        upload_url = reverse("dashboard:tenant_upload_self")
        video_upload_url = reverse("dashboard:tenant_video_upload_self")
        gallery_url = reverse("dashboard:tenant_media_gallery_self")
        settings_url = reverse("dashboard:tenant_site_settings_self")
        blog_url = reverse("dashboard:blog_list_self")
        page_list_url = reverse("dashboard:page_list_self")
        team_url = reverse("dashboard:team_self")
        if page is None:
            preview_url = reverse("dashboard:tenant_preview_self")
            save_url = reverse("dashboard:tenant_save_self")
            publish_url = reverse("dashboard:tenant_publish_self")
            versions_url = reverse("dashboard:tenant_versions_self")
            version_restore_url = reverse("dashboard:tenant_version_restore_self")
            live_url = "/"
        else:
            preview_url = reverse("dashboard:page_preview_self", args=[page.pk])
            save_url = reverse("dashboard:page_save_self", args=[page.pk])
            publish_url = reverse("dashboard:page_publish_self", args=[page.pk])
            # Undo now covers inner pages too (per-page ContentVersion bucket).
            versions_url = reverse("dashboard:page_versions_self", args=[page.pk])
            version_restore_url = reverse("dashboard:page_version_restore_self", args=[page.pk])
            live_url = f"/{page.slug}/"
    else:
        ghl_forms_url = reverse("dashboard:tenant_ghl_forms", args=[tenant.pk])
        upload_url = reverse("dashboard:tenant_upload", args=[tenant.pk])
        video_upload_url = reverse("dashboard:tenant_video_upload", args=[tenant.pk])
        gallery_url = reverse("dashboard:tenant_media_gallery", args=[tenant.pk])
        settings_url = reverse("dashboard:tenant_site_settings", args=[tenant.pk])
        blog_url = reverse("dashboard:blog_list", args=[tenant.pk])
        page_list_url = reverse("dashboard:page_list", args=[tenant.pk])
        team_url = None
        if page is None:
            preview_url = reverse("dashboard:tenant_preview", args=[tenant.pk])
            save_url = reverse("dashboard:tenant_save", args=[tenant.pk])
            publish_url = reverse("dashboard:tenant_publish", args=[tenant.pk])
            versions_url = reverse("dashboard:tenant_versions", args=[tenant.pk])
            version_restore_url = reverse("dashboard:tenant_version_restore", args=[tenant.pk])
            live_url = tenant_public_url(request, tenant)
        else:
            preview_url = reverse("dashboard:page_preview", args=[tenant.pk, page.pk])
            save_url = reverse("dashboard:page_save", args=[tenant.pk, page.pk])
            publish_url = reverse("dashboard:page_publish", args=[tenant.pk, page.pk])
            versions_url = version_restore_url = ""
            live_url = f"{tenant_public_url(request, tenant)}{page.slug}/"

    # Switcher: Home + each inner page, with scope-aware editor URLs.
    if scope == "tenant":
        home_edit_url = reverse("dashboard:tenant_home")
        def _page_edit_url(p):
            return reverse("dashboard:page_editor_self", args=[p.pk])
    else:
        home_edit_url = reverse("dashboard:tenant_editor", args=[tenant.pk])
        def _page_edit_url(p):
            return reverse("dashboard:page_editor", args=[tenant.pk, p.pk])
    page_switch = [{"label": "Home", "url": home_edit_url, "current": page is None}]
    for p in tenant.pages.all():
        page_switch.append({
            "label": p.title,
            "url": _page_edit_url(p),
            "current": page is not None and p.pk == page.pk,
        })

    # Friendly link choices for link fields: this site's own pages (relative to
    # the site root, which is correct on the live subdomain/custom domain), the
    # blog, plus any in-template #anchors the parser already found.
    site_link_targets = [{"value": "/", "label": "Home"}]
    for p in tenant.pages.all():
        site_link_targets.append({"value": f"/{p.slug}/", "label": p.title})
    site_link_targets.append({"value": "/blog/", "label": "Blog"})
    link_targets = site_link_targets + schema.get("link_targets", [])

    client_editable = True
    if scope == "tenant":
        client_editable = _client_may_edit_content(request, editable)

    embed_warnings = []
    if editable.is_published:
        embed_warnings = [
            slot for slot in ghl_embed_slots.get_embed_slots(schema, editable.content)
            if not slot["value"]
        ]

    return render(
        request,
        "dashboard/editor.html",
        {
            "tenant": tenant,
            "editing_page": page,
            # True only when this inner page has its own Template. Shared-shell
            # pages must not offer Edit HTML (A1).
            "page_owns_template": bool(
                page
                and page.template_id
                and tenant.template_id
                and page.template_id != tenant.template_id
            ),
            "target_title": (page.title if page else tenant.name),
            "target_subtitle": (
                f"{page.template.name} · /{page.slug}/" if page
                else f"{tenant.template.name} · {tenant.subdomain}"
            ),
            "target_is_published": editable.is_published,
            "page_switch": page_switch,
            "schema": schema,
            "sections": sections,
            "content_sections": content_sections,
            "nav_sections": nav_sections,
            "header_sections": header_sections,
            "footer_sections": footer_sections,
            "brand_section": brand_section,
            "theme_tokens": theme_tokens,
            "link_targets": link_targets,
            "grouped_sections": grouped,
            # Passed as Python objects and emitted via Django's ``json_script``
            # in the template (see editor.html). ``json_script`` escapes
            # ``<``/``>``/``&`` so a field value like ``</script>`` can't break
            # out of the bootstrap script (E2).
            "content": content,
            "layout_mode": layout_mode,
            "preview_url": preview_url,
            "save_url": save_url,
            "upload_url": upload_url,
            "video_upload_url": video_upload_url,
            "gallery_url": gallery_url,
            "ghl_forms_url": ghl_forms_url,
            "versions_url": versions_url,
            "version_restore_url": version_restore_url,
            "publish_url": publish_url,
            "settings_url": settings_url,
            "blog_url": blog_url,
            "page_list_url": page_list_url,
            "team_url": team_url,
            "live_url": live_url,
            "scope": scope,
            "client_editable": client_editable,
            "embed_warnings": embed_warnings,
            "nav_section": "pages" if scope == "tenant" and page else "editor",
            "block_mode": block_mode,
            "palette": palette,
            "block_defaults": block_defaults,
            "region_name": region_name,
            "shell_regions": (
                _blocks.shell_region_names(tpl.html_source) if block_mode else ["main"]
            ),
            "max_block_depth": _blocks.MAX_BLOCK_DEPTH if block_mode else 0,
            "header_pages": (
                _blocks.editor_header_pages(tenant) if block_mode else []
            ),
        },
    )


def _render_preview(editable):
    from core.services import blocks
    tenant = editable if isinstance(editable, Tenant) else editable.tenant
    html = blocks.render_content(
        editable.template, editable.content, preview=True,
        nav_pages=blocks.nav_pages_for(tenant),
    )
    return HttpResponse(html)


_ALLOWED_STYLE_KEYS = {
    "color", "bgColor", "fontSize", "fontFamily", "fontWeight", "align",
    "lineHeight", "letterSpacing", "textTransform",
    "padding", "margin", "width", "maxWidth", "minHeight", "borderRadius",
    "bgMode", "bgImage", "bgGradient", "bgSize", "bgPosition", "bgOverlay",
    "bgOpacity", "bgBlur",
}
_ALLOWED_GLOBAL_KEYS = {"fontFamily", "baseSize", "headingFamily", "textColor", "pageBg"}


def _clean_style_value(value):
    if isinstance(value, bool):
        return value
    return str(value)[:120]


def _safe_style_key_value(key, value):
    """Validate one _styles/_global value against the same allowlists the
    renderer applies, so unsafe CSS never reaches the DB. Returns the cleaned
    string, or None if it should be dropped (E6)."""
    from core.renderer import (
        _SAFE_STYLE_TOKEN_RE,
        _safe_css_value,
        _safe_gradient_value,
        _safe_blur,
        _safe_opacity,
        _safe_overlay,
        _safe_url_value,
        _sanitize_font_family,
    )

    if key == "bgImage":
        raw = str(value)[:2000].strip()
        return _safe_url_value(raw, allow_data_image=True) or None
    if key == "bgGradient":
        return _safe_gradient_value(str(value)[:80])
    if key == "bgOverlay":
        amount = _safe_overlay(value)
        return str(amount) if amount is not None else None
    if key == "bgOpacity":
        amount = _safe_opacity(value)
        return str(amount) if amount is not None else None
    if key == "bgBlur":
        amount = _safe_blur(value)
        return str(amount) if amount is not None else None
    if key == "bgMode":
        mode = str(value).strip().lower()
        return mode if mode in ("color", "image", "gradient") else None

    raw = str(value)[:120].strip()
    if not raw:
        return None
    if key in ("color", "bgColor", "textColor", "pageBg"):
        return _safe_css_value(raw)
    if key in ("fontFamily", "headingFamily"):
        return _sanitize_font_family(raw) or None
    # fontSize / fontWeight / align / baseSize / lineHeight / letterSpacing /
    # textTransform / layout lengths: numbers and keywords only.
    return raw if _SAFE_STYLE_TOKEN_RE.match(raw) else None


def _normalize_styles(content: dict) -> None:
    """Defensively sanitize the _styles / _global meta namespaces in place so a
    malformed client payload can't inject arbitrary keys the renderer trusts."""
    raw_styles = content.get("_styles")
    if raw_styles is not None:
        clean_styles = {}
        if isinstance(raw_styles, dict):
            for element_id, style in raw_styles.items():
                if not (isinstance(element_id, str) and "." in element_id):
                    continue
                if ".__region." in element_id:
                    _inst, _sep, region = element_id.partition(".__region.")
                    if not (_inst and region and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", region)):
                        continue
                if not isinstance(style, dict):
                    continue
                kept = {}
                for k, v in style.items():
                    if k not in _ALLOWED_STYLE_KEYS or v in (None, ""):
                        continue
                    safe = _safe_style_key_value(k, v)
                    if safe is not None:
                        kept[k] = safe
                if style.get("italic"):
                    kept["italic"] = True
                if kept:
                    clean_styles[element_id[:120]] = kept
        content["_styles"] = clean_styles

    raw_global = content.get("_global")
    if raw_global is not None:
        if isinstance(raw_global, dict):
            clean_global = {}
            for k, v in raw_global.items():
                if k not in _ALLOWED_GLOBAL_KEYS or v in (None, ""):
                    continue
                safe = _safe_style_key_value(k, v)
                if safe is not None:
                    clean_global[k] = safe
            content["_global"] = clean_global
        else:
            content.pop("_global", None)

    # Theme-token overrides: {css-var-name: color/length}. Names restricted to
    # safe CSS identifier chars; values validated so a "red;}body{" can't inject
    # site-wide CSS (E6). Anything else is dropped, not stored.
    raw_tokens = content.get("_tokens")
    if raw_tokens is not None:
        from core.renderer import _SAFE_STYLE_TOKEN_RE, _safe_css_value

        clean_tokens = {}
        if isinstance(raw_tokens, dict):
            for name, value in raw_tokens.items():
                if not isinstance(name, str) or value in (None, ""):
                    continue
                safe_name = re.sub(r"[^a-zA-Z0-9_-]", "", name)[:64]
                raw_val = str(value)[:120].strip()
                safe_val = _safe_css_value(raw_val) or (
                    raw_val if _SAFE_STYLE_TOKEN_RE.match(raw_val) else None
                )
                if safe_name and safe_val:
                    clean_tokens[safe_name] = safe_val
        content["_tokens"] = clean_tokens

    raw_header = content.get("_header")
    if isinstance(raw_header, dict) or isinstance(content.get("regions"), dict):
        from core.services.blocks import ensure_header

        ensure_header(content)
    elif raw_header is not None:
        content.pop("_header", None)


def _save_content(request, editable):
    if not _client_may_edit_content(request, editable):
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "This site isn't set up for editing yet. Contact your agency."
                ),
            },
            status=403,
        )

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    content = payload.get("content")
    if not isinstance(content, dict):
        return HttpResponseBadRequest("content must be an object")

    # Visibility meta: a list of hidden section/field ids. Normalize defensively
    # so a malformed client payload can't break render_site (which iterates it).
    if "_hidden" in content:
        raw_hidden = content.get("_hidden")
        if isinstance(raw_hidden, list):
            content["_hidden"] = [
                str(x)[:120] for x in raw_hidden if isinstance(x, str) and x.strip()
            ][:500]
        else:
            content.pop("_hidden", None)

    _normalize_styles(content)

    template = editable.template

    # Block-instance regions: validate the ordered instance lists against the
    # template's allowlisted palette, enforce the per-page cap, and mint fresh
    # ids for anything missing/duplicated. Rejecting a non-allowlisted block
    # type is what keeps "clients insert only curated blocks" true server-side.
    try:
        _normalize_regions(content, template)
    except _BlockValidationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    schema = (
        build_schema(template.html_source)
        if template and template.html_source
        else ((template.schema if template else None) or {"sections": []})
    )

    # Drop scriptable URLs / unsafe colors from typed field values before they
    # reach the DB. The renderer re-validates on every render, but keeping poison
    # out of storage stops it leaking through the Django admin JSON view (E7).
    _sanitize_content_field_values(content, schema)

    tenant = editable if isinstance(editable, Tenant) else editable.tenant
    try:
        ghl_embed_slots.validate_embed_content_update(
            tenant=tenant,
            schema=schema,
            current_content=editable.content,
            new_content=content,
            is_published=editable.is_published,
        )
    except ghl_embed_slots.GhlEmbedValidationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    # Snapshot-then-save covers the tenant home AND inner pages now (undo works
    # everywhere). Structural block ops go through the same path, so add /
    # remove / reorder are all undoable. Shared with MCP patch_content.
    from core.services import content_versions as cv

    cv.save_editable_content(
        editable,
        content,
        user=request.user,
        source=cv.SOURCE_DASHBOARD,
    )

    return JsonResponse({"ok": True, "updated_at": editable.updated_at.isoformat()})


class _BlockValidationError(Exception):
    """Client sent an invalid regions payload (bad type / over cap)."""


# The exact shape ``core.services.blocks.new_instance_id`` mints:
# ``blk_`` + token_hex(4) == 8 lowercase hex chars. Accept longer hex runs too
# in case the mint width ever grows, but nothing outside [a-f0-9].
# Fresh ids are ``blk_`` + hex (E14). Migrated classic sections keep their
# original slug ids (``hero``, ``about``) so field / style keys still match.
_INSTANCE_ID_RE = re.compile(r"^(?:blk_[a-f0-9]{8,}|[a-z][a-z0-9_-]{0,79})$")


def _sanitize_content_field_values(content: dict, schema: dict) -> None:
    """Validate URL and color field values in ``content`` against the schema so
    a scriptable ``javascript:`` href or CSS-breakout color never persists.

    Unsafe values are replaced with an empty string (the field falls back to its
    template default on render). Mirrors the renderer's per-type allowlists."""
    from core.renderer import _safe_css_value, _safe_url_value

    if not isinstance(content, dict) or not isinstance(schema, dict):
        return
    for section in schema.get("sections") or []:
        sec_id = section.get("id")
        section_data = content.get(sec_id)
        if not isinstance(section_data, dict):
            continue
        for field in section.get("fields") or []:
            ftype = field.get("type")
            field_name = str(field.get("id", "")).split(".")[-1]
            if field_name not in section_data:
                continue
            value = section_data[field_name]
            if not isinstance(value, str) or not value:
                continue
            if ftype == "link":
                safe = _safe_url_value(value, allow_anchor=True)
            elif ftype == "image":
                safe = _safe_url_value(value, allow_data_image=True)
            elif ftype in ("video", "embed"):
                safe = _safe_url_value(value)
            elif ftype == "color":
                safe = _safe_css_value(value) or ""
            else:
                continue
            if safe is None:
                safe = ""
            section_data[field_name] = safe


def _normalize_regions(content: dict, template) -> None:
    """Validate + sanitize ``content['regions']`` in place (no-op if absent).

    Rejects block types not on the template's allowlist, drops unknown instance
    field keys, mints fresh ids for missing/duplicate ones, recurses into layout
    blocks' column children (capped at ``MAX_BLOCK_DEPTH``), and enforces the
    per-page block cap across every nested instance.
    """
    regions = content.get("regions")
    if regions is None:
        return
    if not isinstance(regions, dict) or template is None:
        content.pop("regions", None)
        return

    from core.services import blocks

    catalog = blocks.catalog_for_template(template)
    seen_ids: set[str] = set()
    counter = {"total": 0}

    def clean_instance(inst, depth):
        if not isinstance(inst, dict):
            return None
        itype = inst.get("type")
        if itype not in catalog:
            raise _BlockValidationError("That block isn't available on this site.")
        # Instance ids are emitted into HTML attributes and used verbatim in
        # querySelector lookups, so restrict them to the exact shape
        # ``new_instance_id`` mints (``blk_`` + hex). Anything else — including a
        # crafted id carrying markup — is replaced with a fresh safe id (E14).
        iid = str(inst.get("id") or "").strip()
        if not _INSTANCE_ID_RE.match(iid) or iid in seen_ids:
            iid = blocks.new_instance_id()
        seen_ids.add(iid)

        known_fields = {
            f["id"] for f in (catalog[itype]["schema"].get("fields") or [])
        }
        raw_fields = inst.get("fields")
        clean_fields = {}
        if isinstance(raw_fields, dict):
            for key, value in raw_fields.items():
                if key in known_fields and isinstance(key, str):
                    clean_fields[key] = value
        counter["total"] += 1
        out = {"id": iid, "type": itype, "fields": clean_fields}

        # Layout blocks carry column slots; keep only children dropped into a
        # real slot name.
        slot_names = catalog[itype].get("regions") or []
        raw_children = inst.get("children")
        if slot_names and depth < blocks.MAX_BLOCK_DEPTH:
            children: dict[str, list] = {}
            for name in slot_names:
                child_list = []
                if isinstance(raw_children, dict) and isinstance(
                    raw_children.get(name), list
                ):
                    for child in raw_children[name]:
                        cleaned = clean_instance(child, depth + 1)
                        if cleaned is not None:
                            child_list.append(cleaned)
                children[name] = child_list
            out["children"] = children
        elif slot_names and isinstance(raw_children, dict):
            # At the depth cap. Silently dropping children here is data loss
            # (the client sees their nested rows vanish on save), so reject the
            # save instead and let the UI keep the pre-save state (E11).
            for name in slot_names:
                if isinstance(raw_children.get(name), list) and raw_children[name]:
                    raise _BlockValidationError(
                        "Blocks are nested too deeply to save. "
                        "Move inner blocks up a level and try again."
                    )
        return out

    clean: dict[str, list] = {}
    for region_name, instances in regions.items():
        if not isinstance(region_name, str) or not isinstance(instances, list):
            continue
        rname = re.sub(r"[^a-zA-Z0-9_-]", "", region_name)[:40] or "main"
        clean_list = []
        for inst in instances:
            cleaned = clean_instance(inst, 0)
            if cleaned is not None:
                clean_list.append(cleaned)
        clean[rname] = clean_list

    if counter["total"] > blocks.MAX_BLOCKS_PER_PAGE:
        raise _BlockValidationError(
            f"This page can have at most {blocks.MAX_BLOCKS_PER_PAGE} sections."
        )
    content["regions"] = clean


def _ghl_forms_json(tenant):
    try:
        forms = ghl_forms.list_forms_for_tenant(tenant)
    except ghl_forms.GhlFormsUnavailable as exc:
        status = 503 if exc.code == "temporarily_unavailable" else 409
        return JsonResponse(
            {"ok": False, "code": exc.code, "error": exc.public_message},
            status=status,
        )
    return JsonResponse(
        {
            "ok": True,
            "forms": [
                {**form, "value": f'form:{form["id"]}'}
                for form in forms
            ],
        }
    )


# --------------------------------------------------------------------------- #
# Version history / undo                                                        #
# --------------------------------------------------------------------------- #


def _versions_list(tenant, scope):
    from core.services import content_versions as cv

    items = []
    # Only the client's own dashboard snapshots are offered for undo. MCP writes
    # live in a separate bucket; surfacing them here would let "Undo" jump the
    # client into an AI-generated state they never chose (E12).
    versions = (
        tenant.versions.select_related("saved_by")
        .filter(page__isnull=True, source=cv.SOURCE_DASHBOARD)
        .order_by("-saved_at")[:25]
    )
    for v in versions:
        if scope == "tenant":
            preview_url = reverse("dashboard:tenant_version_preview_self", args=[v.id])
        else:
            preview_url = reverse("dashboard:tenant_version_preview", args=[tenant.pk, v.id])
        items.append({
            "id": v.id,
            "saved_at": v.saved_at.isoformat(),
            "saved_by": v.saved_by.username if v.saved_by else "unknown",
            "preview_url": preview_url,
        })
    return JsonResponse({"ok": True, "versions": items})


def _version_restore(request, tenant):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    version = tenant.versions.filter(
        page__isnull=True, id=payload.get("version_id")
    ).first()
    if version is None:
        return JsonResponse({"ok": False, "error": "That version no longer exists."}, status=404)

    # pop=True is the editor's linear Undo (consume the snapshot, no redo point);
    # pop=False is an arbitrary history restore (snapshot current first).
    pop = bool(payload.get("pop"))
    from core.services import content_versions as cv

    try:
        cv.restore_tenant_content(tenant, version, user=request.user, pop=pop)
    except (ghl_embed_slots.GhlEmbedValidationError, cv.RestoreValidationError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True})


def _version_preview(tenant, version_id):
    from core.services import blocks

    version = get_object_or_404(
        tenant.versions.filter(page__isnull=True), id=version_id
    )
    html = blocks.render_content(tenant.template, version.snapshot, preview=True)
    return HttpResponse(html)


# --------------------------------------------------------------------------- #
# Per-page version history / undo (tenant self scope)                          #
# --------------------------------------------------------------------------- #


def _page_versions_list(page):
    from core.services import content_versions as cv

    items = []
    # Dashboard-only, same rationale as the tenant-home list (E12).
    _pv = page.versions.select_related("saved_by").filter(source=cv.SOURCE_DASHBOARD)
    for v in _pv.order_by("-saved_at")[:25]:
        items.append({
            "id": v.id,
            "saved_at": v.saved_at.isoformat(),
            "saved_by": v.saved_by.username if v.saved_by else "unknown",
            "preview_url": reverse(
                "dashboard:page_version_preview_self", args=[page.pk, v.id]
            ),
        })
    return JsonResponse({"ok": True, "versions": items})


def _page_version_restore(request, page):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    version = page.versions.filter(id=payload.get("version_id")).first()
    if version is None:
        return JsonResponse({"ok": False, "error": "That version no longer exists."}, status=404)

    pop = bool(payload.get("pop"))
    from core.services import content_versions as cv

    try:
        cv.restore_editable_content(page, version, user=request.user, pop=pop)
    except (ghl_embed_slots.GhlEmbedValidationError, cv.RestoreValidationError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True})


def _page_version_preview(page, version_id):
    from core.services import blocks

    version = get_object_or_404(page.versions, id=version_id)
    html = blocks.render_content(page.template, version.snapshot, preview=True)
    return HttpResponse(html)


@tenant_member_required
@require_GET
def tenant_versions_self(request):
    return _versions_list(request.tenant, "tenant")


@tenant_member_required
@require_POST
def tenant_version_restore_self(request):
    return _version_restore(request, request.tenant)


@tenant_member_required
@require_GET
def tenant_version_preview_self(request, version_id):
    return _version_preview(request.tenant, version_id)


@tenant_member_required
@require_GET
def page_versions_self(request, page_pk):
    page = _get_tenant_page(request.tenant, page_pk)
    return _page_versions_list(page)


@tenant_member_required
@require_POST
def page_version_restore_self(request, page_pk):
    page = _get_tenant_page(request.tenant, page_pk)
    return _page_version_restore(request, page)


@tenant_member_required
@require_GET
def page_version_preview_self(request, page_pk, version_id):
    page = _get_tenant_page(request.tenant, page_pk)
    return _page_version_preview(page, version_id)


@agency_operator_required
@require_GET
def tenant_versions(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _versions_list(tenant, "agency")


@agency_operator_required
@require_POST
def tenant_version_restore(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _version_restore(request, tenant)


@agency_operator_required
@require_GET
def tenant_version_preview(request, pk, version_id):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _version_preview(tenant, version_id)


def _toggle_publish(request, editable, *, redirect_url, noun="Site"):
    editable.is_published = not editable.is_published
    editable.save(update_fields=["is_published", "updated_at"])
    state = "published" if editable.is_published else "unpublished"
    messages.success(request, f"{noun} {state}.")
    return redirect(redirect_url)


_CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", re.I)


def _usable_image_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if not u or u.startswith("#"):
        return False
    lower = u.lower()
    if lower.startswith("data:") or lower.startswith("javascript:"):
        return False
    return True


def _gallery_display_url(url: str) -> bool:
    """URLs we will actually show as tiles. Legacy Cloudinary links 401 now
    that media lives on Iceberg — listing them just produces broken images."""
    if not _usable_image_url(url):
        return False
    return "res.cloudinary.com" not in url.lower()


def _filename_from_url(url: str) -> str:
    from urllib.parse import unquote, urlparse

    path = urlparse(url.strip()).path
    name = unquote(path.rsplit("/", 1)[-1] if path else "")
    return name or "Image"


def _urls_from_srcset(srcset: str) -> list[str]:
    urls = []
    for part in (srcset or "").split(","):
        token = part.strip().split()
        if token:
            urls.append(token[0])
    return urls


def _harvest_html_image_urls(html: str) -> list[str]:
    """Pull every image URL out of a page's HTML (img/srcset + css url())."""
    from bs4 import BeautifulSoup

    if not html or not html.strip():
        return []
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []

    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            found.append(src)
        if img.get("srcset"):
            found.extend(_urls_from_srcset(img["srcset"]))

    for source in soup.find_all("source"):
        if source.get("srcset"):
            found.extend(_urls_from_srcset(source["srcset"]))
        src = source.get("src")
        if src:
            found.append(src)

    for el in soup.find_all(style=True):
        for match in _CSS_URL_RE.finditer(el.get("style") or ""):
            found.append(match.group(1))

    for style in soup.find_all("style"):
        text = style.string or style.get_text() or ""
        for match in _CSS_URL_RE.finditer(text):
            found.append(match.group(1))

    return [u.strip() for u in found if _usable_image_url(u)]


_IMAGE_URL_EXT_RE = re.compile(
    r"\.(?:png|jpe?g|gif|webp|svg)(?:\?|#|$)", re.IGNORECASE
)
_IMAGE_CONTENT_KEYS = {
    "bgimage", "bg_image", "image", "photo", "logo", "src", "img",
}


def _looks_like_image_url(value: str) -> bool:
    if not _usable_image_url(value):
        return False
    lower = value.lower()
    if _IMAGE_URL_EXT_RE.search(lower):
        return True
    return "/image/" in lower or "/images/" in lower or "cloudinary" in lower


def _harvest_content_image_urls(content) -> list[str]:
    """Pick image URLs out of a page JSON blob (block fields, backgrounds)."""
    found: list[str] = []

    def walk(obj, key=""):
        if isinstance(obj, dict):
            for child_key, value in obj.items():
                walk(value, str(child_key))
            return
        if isinstance(obj, list):
            for value in obj:
                walk(value, key)
            return
        if isinstance(obj, str) and (
            key.lower() in _IMAGE_CONTENT_KEYS or _looks_like_image_url(obj)
        ):
            if _usable_image_url(obj):
                found.append(obj.strip())

    walk(content)
    return found


def _harvest_schema_image_urls(schema: dict, content: dict) -> list[str]:
    """Image field values after merge (covers content overrides of template defaults)."""
    image_field_ids: list[str] = []
    for section in (schema or {}).get("sections") or []:
        for field in section.get("fields") or []:
            if field.get("type") == "image" and field.get("id"):
                image_field_ids.append(field["id"])

    merged = merge_with_defaults(schema or {}, content or {})
    found: list[str] = []
    for field_id in image_field_ids:
        parts = field_id.split(".", 1)
        if len(parts) != 2:
            continue
        value = (merged.get(parts[0]) or {}).get(parts[1])
        if _usable_image_url(value):
            found.append(value.strip())
    return found


def _media_gallery_list(tenant):
    """All images for a tenant: uploads + every image used on their pages."""
    seen: set[str] = set()
    items: list[dict] = []

    def add(url, *, name=None, asset_id=None, nbytes=0, uploaded_at="", source="upload"):
        key = (url or "").strip()
        if not _gallery_display_url(key) or key in seen:
            return
        seen.add(key)
        items.append({
            "id": asset_id,
            "url": key,
            "name": name or _filename_from_url(key),
            "bytes": nbytes or 0,
            "uploaded_at": uploaded_at or "",
            "source": source,
            # Only uploaded MediaAssets can be renamed/deleted; template
            # defaults and harvested page images are read-only in the gallery.
            "editable": source == "upload" and asset_id is not None,
        })

    # 1) Explicit uploads first (newest).
    for asset in tenant.assets.filter(resource_type=MediaAsset.RESOURCE_IMAGE).order_by(
        "-uploaded_at"
    ):
        add(
            asset.url,
            name=asset.original_name or "Image",
            asset_id=asset.id,
            nbytes=asset.bytes,
            uploaded_at=asset.uploaded_at.isoformat() if asset.uploaded_at else "",
            source="upload",
        )

    # 2) Home landing page (template HTML + editable image fields).
    home_tpl = tenant.template
    if home_tpl is not None:
        for url in _harvest_html_image_urls(home_tpl.html_source):
            add(url, source="page")
        for url in _harvest_schema_image_urls(home_tpl.schema or {}, tenant.content or {}):
            add(url, source="page")
    for url in _harvest_content_image_urls(tenant.content or {}):
        add(url, source="page")

    # 3) Inner pages.
    for page in tenant.pages.select_related("template").all():
        tpl = page.template
        if tpl is None:
            continue
        for url in _harvest_html_image_urls(tpl.html_source):
            add(url, source="page")
        for url in _harvest_schema_image_urls(tpl.schema or {}, page.content or {}):
            add(url, source="page")
        for url in _harvest_content_image_urls(page.content or {}):
            add(url, source="page")

    # 4) Social share image from site settings, if any.
    og = (tenant.site_settings or {}).get("og_image_url") or ""
    if og:
        add(og.strip(), name="Social share image", source="settings")

    return JsonResponse({"ok": True, "assets": items})


def _scrub_url_from_content(content: dict, url: str) -> tuple[dict, bool]:
    """Remove exact URL matches from section field values so deletes fall back
    to template defaults. Returns (new_content, changed)."""
    if not isinstance(content, dict) or not url:
        return content or {}, False
    changed = False
    new: dict = {}
    for key, value in content.items():
        if isinstance(key, str) and key.startswith("_"):
            new[key] = value
            continue
        if isinstance(value, dict):
            section = {}
            for field_key, field_val in value.items():
                if field_val == url:
                    changed = True
                    continue
                section[field_key] = field_val
            new[key] = section
        else:
            new[key] = value
    return new, changed


def _media_item_mutate(request, tenant, asset_id):
    """Rename (POST) or delete (DELETE) an uploaded MediaAsset.

    Default / harvested page images have no MediaAsset id and cannot reach
    this endpoint. The gallery UI also hides those actions for them.
    """
    asset = tenant.assets.filter(
        pk=asset_id, resource_type=MediaAsset.RESOURCE_IMAGE
    ).first()
    if asset is None:
        return JsonResponse(
            {"ok": False, "error": "That image isn't in your uploads."},
            status=404,
        )

    if request.method == "DELETE":
        url = asset.url
        asset.delete()
        if url:
            # Deleting an in-use image rewrites content (fields fall back to the
            # template default). Route that through save_editable_content so it
            # snapshots first and stays undoable — a raw .save() would silently
            # drop the image with no way back (C3).
            from core.services import content_versions as cv

            new_content, changed = _scrub_url_from_content(tenant.content or {}, url)
            if changed:
                cv.save_editable_content(
                    tenant, new_content, user=request.user, source=cv.SOURCE_DASHBOARD
                )
            for page in tenant.pages.all():
                page_content, page_changed = _scrub_url_from_content(
                    page.content or {}, url
                )
                if page_changed:
                    cv.save_editable_content(
                        page, page_content, user=request.user,
                        source=cv.SOURCE_DASHBOARD,
                    )
        return JsonResponse({"ok": True})

    # POST → rename
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    name = (payload.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
    if len(name) > 240:
        return JsonResponse(
            {"ok": False, "error": "Name must be 240 characters or fewer."},
            status=400,
        )

    asset.original_name = name
    asset.save(update_fields=["original_name"])
    return JsonResponse({"ok": True, "id": asset.id, "name": asset.original_name})


def _save_upload(request, tenant):
    """Image upload: validated at the door, then stored on Iceberg and served
    from cdn.katalyst-crm.com. Returns a clear error the editor can display."""
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"ok": False, "error": "No file received."}, status=400)

    ok, error = iceberg_media.validate_image(upload)
    if not ok:
        return JsonResponse({"ok": False, "error": error}, status=400)

    if not iceberg_media.is_configured():
        return JsonResponse(
            {"ok": False, "error": "Image storage isn't configured."}, status=500
        )

    try:
        result = iceberg_media.upload_image(upload, tenant)
    except Exception:
        logger.exception("Iceberg image upload failed for tenant %s", tenant.pk)
        return JsonResponse(
            {"ok": False, "error": "Upload failed. Please try again."}, status=502
        )

    asset = MediaAsset.objects.create(
        tenant=tenant,
        original_name=upload.name[:240],
        resource_type=MediaAsset.RESOURCE_IMAGE,
        public_id=result["public_id"],
        secure_url=result["secure_url"],
        bytes=result.get("bytes", 0),
    )
    return JsonResponse({"ok": True, "url": result["delivery_url"], "id": asset.id})


def _save_video_upload(request, tenant):
    """Video upload: streamed through our server to Iceberg (no browser-direct
    upload because R2 blocks cross-origin PUT from tenant domains)."""
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"ok": False, "error": "No file received."}, status=400)

    if not iceberg_media.is_configured():
        return JsonResponse(
            {"ok": False, "error": "Video storage isn't configured."}, status=500
        )

    info, error = iceberg_media.upload_video(upload, tenant)
    if error:
        return JsonResponse({"ok": False, "error": error}, status=400)

    asset = MediaAsset.objects.create(
        tenant=tenant,
        original_name=upload.name[:240],
        resource_type=MediaAsset.RESOURCE_VIDEO,
        public_id=info["public_id"],
        secure_url=info["secure_url"],
        bytes=info.get("bytes", 0),
    )
    return JsonResponse({"ok": True, "url": info["secure_url"], "id": asset.id})


# --------------------------------------------------------------------------- #
# Site settings (SEO, analytics, custom head)                                   #
# --------------------------------------------------------------------------- #


def _validate_site_settings(data):
    """Validate and clean site settings dict. Returns (cleaned, errors)."""
    if not isinstance(data, dict):
        return {}, ["Request body must be a JSON object."]

    errors = []
    cleaned = {}

    page_title = (data.get("page_title") or "")
    if not isinstance(page_title, str):
        page_title = ""
    page_title = page_title.strip()
    if len(page_title) > 200:
        errors.append("Page title must be 200 characters or fewer.")
    cleaned["page_title"] = page_title

    meta_desc = (data.get("meta_description") or "")
    if not isinstance(meta_desc, str):
        meta_desc = ""
    meta_desc = meta_desc.strip()
    if len(meta_desc) > 500:
        errors.append("Meta description must be 500 characters or fewer.")
    cleaned["meta_description"] = meta_desc

    og_image = (data.get("og_image_url") or "")
    if not isinstance(og_image, str):
        og_image = ""
    og_image = og_image.strip()
    if og_image and not og_image.startswith(("http://", "https://", "/")):
        errors.append("OG image URL must start with http://, https://, or /.")
    cleaned["og_image_url"] = og_image

    ga_id = (data.get("ga_measurement_id") or "")
    if not isinstance(ga_id, str):
        ga_id = ""
    ga_id = ga_id.strip()
    if ga_id and not GA_ID_RE.match(ga_id):
        errors.append("GA Measurement ID must be like G-XXXXXXX or UA-XXXXX-X.")
    cleaned["ga_measurement_id"] = ga_id

    custom_script = (data.get("custom_head_script") or "")
    if not isinstance(custom_script, str):
        custom_script = ""
    cleaned["custom_head_script"] = custom_script.strip()

    return cleaned, errors


def _get_or_save_site_settings(request, tenant):
    """Shared GET/POST handler for site settings endpoints."""
    if request.method == "GET":
        return JsonResponse({"settings": tenant.site_settings or {}})

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    cleaned, errors = _validate_site_settings(payload)
    if errors:
        return JsonResponse({"errors": errors}, status=400)

    tenant.site_settings = cleaned
    tenant.save(update_fields=["site_settings", "updated_at"])
    return JsonResponse({"ok": True, "settings": cleaned})


@agency_operator_required
def tenant_site_settings(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _get_or_save_site_settings(request, tenant)


@tenant_member_required
def tenant_site_settings_self(request):
    return _get_or_save_site_settings(request, request.tenant)


# --------------------------------------------------------------------------- #
# Custom domain (direct-to-origin + Let's Encrypt): agency surface            #
# --------------------------------------------------------------------------- #


# Common two-label public suffixes. Used so ``example.co.uk`` is treated as
# an apex (DNS name ``@``) rather than a host ``example`` (A15). Not a full
# PSL — no extra dependency.
_MULTI_PART_TLDS = frozenset({
    ("co", "uk"), ("org", "uk"), ("ac", "uk"), ("gov", "uk"),
    ("com", "au"), ("net", "au"), ("org", "au"),
    ("co", "nz"), ("net", "nz"), ("org", "nz"),
    ("co", "za"), ("org", "za"),
    ("com", "br"), ("com", "mx"),
    ("co", "jp"), ("ne", "jp"),
    ("com", "sg"), ("com", "hk"),
    ("co", "in"), ("com", "in"),
})


def _dns_name_for_domain(domain: str) -> str:
    """The record NAME to enter at the registrar: ``@`` for a root domain
    (2 labels, or 3 when the last two are a known public suffix like
    ``co.uk``), else the leftmost label (``www``, ``training``, …).
    """
    cleaned = (domain or "").strip().rstrip(".")
    if not cleaned:
        return "@"
    labels = cleaned.split(".")
    apex_len = 3 if (
        len(labels) >= 3
        and tuple(label.lower() for label in labels[-2:]) in _MULTI_PART_TLDS
    ) else 2
    if len(labels) <= apex_len:
        return "@"
    return labels[0]


def _custom_domain_context(tenant):
    """Shared context for the custom-domain panel. Returns every one of the
    tenant's domains (oldest first, stable order) with its DNS record name, plus
    the origin IP. Used by both the initial tenant_detail render and the
    fetch-swap partial so the two never drift out of sync."""
    domains = [
        {"obj": cd, "dns_name": _dns_name_for_domain(cd.domain)}
        for cd in tenant.custom_domains.order_by("created_at")
    ]
    verified = sum(1 for d in domains if d["obj"].is_verified)
    return {
        "custom_domains": domains,
        "custom_domains_verified": verified,
        "custom_domains_pending": len(domains) - verified,
        "target_ip": settings.CUSTOM_DOMAIN_TARGET_IP,
    }


def _sync_tenant_primary_domain(tenant) -> None:
    """Keep the vestigial ``Tenant.custom_domain`` display hint in step with the
    CustomDomain table: the earliest verified domain (or "" when none). Routing
    keys off the CustomDomain rows; this only feeds the site_created / detail
    display so it must not drift after add/verify/delete (A17)."""
    primary = (
        tenant.custom_domains.filter(is_verified=True)
        .order_by("created_at")
        .values_list("domain", flat=True)
        .first()
        or ""
    )
    if tenant.custom_domain != primary:
        tenant.custom_domain = primary
        tenant.save(update_fields=["custom_domain", "updated_at"])


def _render_custom_domain_partial(request, tenant, *, error=None, info=None):
    context = _custom_domain_context(tenant)
    context.update({"tenant": tenant, "error": error, "info": info})
    return render(request, "dashboard/partials/custom_domain.html", context)


@agency_operator_required
@require_GET
def tenant_custom_domain_section(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _render_custom_domain_partial(request, tenant)


@agency_operator_required
@require_POST
def tenant_custom_domain_add(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    _created, error = custom_domains.add_custom_domain(
        tenant, request.POST.get("domain") or ""
    )
    if error:
        return _render_custom_domain_partial(request, tenant, error=error)
    _sync_tenant_primary_domain(tenant)
    return _render_custom_domain_partial(request, tenant)


@agency_operator_required
@require_POST
def tenant_custom_domain_verify(request, pk, domain_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    # Tenant-scoped lookup: a domain_pk belonging to another tenant 404s rather
    # than letting one tenant's page act on another tenant's domain.
    custom_domain = get_object_or_404(CustomDomain, pk=domain_pk, tenant=tenant)

    verified, resolved = custom_domains.verify_custom_domain(custom_domain)

    if verified:
        _sync_tenant_primary_domain(tenant)
        return _render_custom_domain_partial(
            request, tenant,
            info="DNS verified. Your SSL certificate is issued automatically "
                 "within about a minute on first visit. Then your domain is live.",
        )

    if resolved:
        detail = f"it currently points at {', '.join(resolved)}"
    else:
        detail = "it isn't resolving yet (DNS can take a few minutes to propagate)"
    return _render_custom_domain_partial(
        request, tenant,
        info=(
            f"Not verified yet. {custom_domain.domain} should point at "
            f"{settings.CUSTOM_DOMAIN_TARGET_IP}, but {detail}. Add the A "
            "record at your registrar, then check again."
        ),
    )


@agency_operator_required
@require_POST
def tenant_custom_domain_delete(request, pk, domain_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    custom_domain = get_object_or_404(CustomDomain, pk=domain_pk, tenant=tenant)

    # Deleting the row drops the host from the next route-syncer pass (≤20s), so
    # Traefik stops routing it. No external (Cloudflare/Railway) cleanup needed.
    custom_domain.delete()
    _sync_tenant_primary_domain(tenant)
    return _render_custom_domain_partial(request, tenant)


# --------------------------------------------------------------------------- #
# Custom domain: agency-wide list + override actions                          #
# --------------------------------------------------------------------------- #


@agency_operator_required
def custom_domain_list(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "all").lower()

    domains = (
        CustomDomain.objects.select_related("tenant")
        .order_by("-created_at")
    )
    if q:
        domains = domains.filter(
            Q(domain__icontains=q)
            | Q(tenant__name__icontains=q)
            | Q(tenant__subdomain__icontains=q)
        )
    if status == "verified":
        domains = domains.filter(is_verified=True)
    elif status == "pending":
        domains = domains.filter(is_verified=False)

    return render(
        request,
        "dashboard/custom_domain_list.html",
        {
            "domains": domains,
            "q": q,
            "status": status,
            "nav_section": "domains",
        },
    )


@agency_operator_required
@require_POST
def custom_domain_force_verify(request, pk):
    # Force-verify bypasses the real HTTP-01/DNS check and immediately routes a
    # domain — a strong override reserved for superusers (A10).
    if not request.user.is_superuser:
        return HttpResponseForbidden(
            "Only a superuser can force-verify a custom domain."
        )
    domain = get_object_or_404(CustomDomain, pk=pk)
    if not domain.is_verified:
        domain.is_verified = True
        domain.save(update_fields=["is_verified", "updated_at"])
        _sync_tenant_primary_domain(domain.tenant)
        messages.success(request, f"“{domain.domain}” force-marked as verified.")
    else:
        messages.info(request, f"“{domain.domain}” was already verified.")
    return redirect("dashboard:custom_domain_list")


@agency_operator_required
@require_POST
def custom_domain_force_delete_local(request, pk):
    domain = get_object_or_404(CustomDomain, pk=pk)
    label = domain.domain
    tenant = domain.tenant
    domain.delete()
    _sync_tenant_primary_domain(tenant)
    messages.success(
        request,
        f"“{label}” deleted. It drops from Traefik on the next route sync (≤20s).",
    )
    return redirect("dashboard:custom_domain_list")


# --------------------------------------------------------------------------- #
# Blog: shared helpers (two surfaces: agency by pk, tenant by host)           #
# --------------------------------------------------------------------------- #


BLOG_STRIP_MAX = 6


def _blog_nav_urls(scope, tenant):
    """Reverse the per-surface blog dashboard URLs + the reused upload URL."""
    if scope == "tenant":
        return {
            "list": reverse("dashboard:blog_list_self"),
            "create": reverse("dashboard:blog_create_self"),
            "reorder": reverse("dashboard:blog_reorder_self"),
            "settings": reverse("dashboard:blog_settings_self"),
            "upload": reverse("dashboard:tenant_upload_self"),
            "preview_new": reverse("dashboard:blog_preview_new_self"),
            "sanitize": reverse("dashboard:blog_sanitize_self"),
            "strip_preview": reverse("dashboard:blog_strip_preview_self"),
            "back": reverse("dashboard:tenant_home"),
            "editor": reverse("dashboard:tenant_home"),
            "public_base": "/blog/",
        }
    return {
        "list": reverse("dashboard:blog_list", args=[tenant.pk]),
        "create": reverse("dashboard:blog_create", args=[tenant.pk]),
        "reorder": reverse("dashboard:blog_reorder", args=[tenant.pk]),
        "settings": reverse("dashboard:blog_settings", args=[tenant.pk]),
        "upload": reverse("dashboard:tenant_upload", args=[tenant.pk]),
        "preview_new": reverse("dashboard:blog_preview_new", args=[tenant.pk]),
        "sanitize": reverse("dashboard:blog_sanitize", args=[tenant.pk]),
        "strip_preview": reverse("dashboard:blog_strip_preview", args=[tenant.pk]),
        "back": reverse("dashboard:tenant_detail", args=[tenant.pk]),
        "editor": reverse("dashboard:tenant_editor", args=[tenant.pk]),
        "public_base": f"/site/{tenant.subdomain}/blog/",
    }


def _blog_post_urls(scope, tenant, post):
    base = _blog_nav_urls(scope, tenant)["public_base"]
    if scope == "tenant":
        return {
            "edit": reverse("dashboard:blog_edit_self", args=[post.pk]),
            "delete": reverse("dashboard:blog_delete_self", args=[post.pk]),
            "featured": reverse("dashboard:blog_featured_toggle_self", args=[post.pk]),
            "preview": reverse("dashboard:blog_preview_self", args=[post.pk]),
            "view": f"{base}{post.slug}/",
        }
    return {
        "edit": reverse("dashboard:blog_edit", args=[tenant.pk, post.pk]),
        "delete": reverse("dashboard:blog_delete", args=[tenant.pk, post.pk]),
        "featured": reverse("dashboard:blog_featured_toggle", args=[tenant.pk, post.pk]),
        "preview": reverse("dashboard:blog_preview", args=[tenant.pk, post.pk]),
        "view": f"{base}{post.slug}/",
    }


def _blog_post_to_form(post):
    if post is None:
        return {
            "title": "", "slug": "", "cover_image": "", "excerpt": "",
            "body": "", "author": "", "status": BlogPost.STATUS_DRAFT,
            "publish_date": "", "seo_title": "", "seo_description": "",
            "og_image_url": "", "template": "", "featured": False,
        }
    pub = (
        timezone.localtime(post.publish_date).strftime("%Y-%m-%dT%H:%M")
        if post.publish_date else ""
    )
    return {
        "title": post.title, "slug": post.slug, "cover_image": post.cover_image,
        "excerpt": post.excerpt, "body": post.body, "author": post.author,
        "status": post.status, "publish_date": pub,
        "seo_title": post.seo_title, "seo_description": post.seo_description,
        "og_image_url": post.og_image_url, "template": post.template,
        "featured": post.featured,
    }


def _blog_list(request, tenant, scope):
    status = (request.GET.get("status") or "all").lower()
    posts_qs = tenant.blog_posts.all()
    if status == "published":
        posts_qs = posts_qs.filter(status=BlogPost.STATUS_PUBLISHED)
    elif status == "draft":
        posts_qs = posts_qs.filter(status=BlogPost.STATUS_DRAFT)
    posts_qs = posts_qs.order_by("-updated_at")

    rows = [{"post": p, "urls": _blog_post_urls(scope, tenant, p)} for p in posts_qs]
    featured = (
        tenant.blog_posts.filter(featured=True)
        .order_by("featured_order", "-publish_date")
    )
    featured_rows = [
        {"post": p, "urls": _blog_post_urls(scope, tenant, p)} for p in featured
    ]

    return render(
        request,
        "dashboard/blog_list.html",
        {
            "tenant": tenant,
            "scope": scope,
            "rows": rows,
            "featured_rows": featured_rows,
            "status": status,
            "blog_urls": _blog_nav_urls(scope, tenant),
            "blog_settings": blog_render.get_blog_settings(tenant),
            "template_choices": BLOG_TEMPLATE_CHOICES,
            "strip_choices": BLOG_STRIP_CHOICES,
            "strip_max": BLOG_STRIP_MAX,
            "nav_section": "blog",
        },
    )


def _blog_form(request, tenant, scope, post):
    if request.method == "POST":
        return _blog_save(request, tenant, scope, post)
    return _blog_render_form(request, tenant, scope, post)


def _blog_render_form(request, tenant, scope, post, *, form_data=None, errors=None, status=200):
    nav = _blog_nav_urls(scope, tenant)
    if post is not None:
        urls = _blog_post_urls(scope, tenant, post)
        save_url, delete_url, view_url = urls["edit"], urls["delete"], urls["view"]
        preview_url = urls["preview"]
    else:
        save_url, delete_url, view_url = nav["create"], None, None
        preview_url = nav["preview_new"]

    return render(
        request,
        "dashboard/blog_form.html",
        {
            "tenant": tenant,
            "scope": scope,
            "post": post,
            "form": form_data if form_data is not None else _blog_post_to_form(post),
            "errors": errors or [],
            "save_url": save_url,
            "delete_url": delete_url,
            "view_url": view_url,
            "preview_url": preview_url,
            "blog_urls": nav,
            "default_blog_style": blog_render.get_blog_settings(tenant)["template"],
            "template_choices": BLOG_TEMPLATE_CHOICES,
            "status_choices": BlogPost.STATUS_CHOICES,
            "nav_section": "blog",
        },
        status=status,
    )


def _blog_preview(request, tenant, scope, post):
    """Server-rendered live preview of a single post (saved or unsaved).

    Rendered with the bridge script so blog_editor.js can patch title/body/
    cover in place. ``?style=`` forces a blog style for live style switching.
    """
    if post is None:
        post = BlogPost(tenant=tenant, title="Untitled post")
    style = (request.GET.get("style") or "").strip()
    html, _ = blog_render.render_detail(
        tenant,
        post,
        style=style or None,
        request=request,
        blog_base=_blog_nav_urls(scope, tenant)["public_base"],
        preview_bridge=True,
        is_preview=False,
    )
    return HttpResponse(html)


def _blog_sanitize(request):
    """Return the post body sanitized exactly as the public render sanitizes.

    The live preview patches the post body into the iframe; doing so with raw
    contenteditable HTML would (a) be a self-XSS vector and (b) diverge from
    the public page, which strips it. Rather than fork the allowlist into JS,
    the editor round-trips the body through this endpoint so the preview body
    is byte-identical to what the public site renders. Single source of truth.
    """
    body = request.POST.get("body") or ""
    return JsonResponse({"html": sanitize_html(body)})


def _blog_strip_preview(request, tenant, scope):
    """Live homepage-strip preview honoring *unsaved* settings overrides."""
    g = request.GET
    enabled = None
    if "enabled" in g:
        enabled = g.get("enabled") in ("1", "true", "on", "yes")
    html = blog_render.render_strip_doc(
        tenant,
        strip_style=(g.get("strip_style") or "").strip() or None,
        count=g.get("count"),
        heading=g.get("heading"),
        enabled=enabled,
        request=request,
        blog_base=_blog_nav_urls(scope, tenant)["public_base"],
    )
    return HttpResponse(html)


def _blog_save(request, tenant, scope, post):
    title = (request.POST.get("title") or "").strip()
    slug_in = (request.POST.get("slug") or "").strip()
    cover_image = (request.POST.get("cover_image") or "").strip()
    excerpt = (request.POST.get("excerpt") or "").strip()
    body = sanitize_html(request.POST.get("body") or "")
    author = (request.POST.get("author") or "").strip()
    status = (request.POST.get("status") or BlogPost.STATUS_DRAFT).strip()
    publish_in = (request.POST.get("publish_date") or "").strip()
    seo_title = (request.POST.get("seo_title") or "").strip()
    seo_description = (request.POST.get("seo_description") or "").strip()
    og_image_url = (request.POST.get("og_image_url") or "").strip()
    template_override = (request.POST.get("template") or "").strip()
    featured = (request.POST.get("featured") or "") in ("on", "true", "1", "yes")

    if status not in dict(BlogPost.STATUS_CHOICES):
        status = BlogPost.STATUS_DRAFT
    if template_override and template_override not in BLOG_TEMPLATE_IDS:
        template_override = ""

    errors = []
    if not title:
        errors.append("Title is required.")

    publish_date = None
    if publish_in:
        parsed = parse_datetime(publish_in)
        if parsed is None:
            errors.append("Publish date isn't a valid date/time.")
        else:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            publish_date = parsed

    form_data = {
        "title": title, "slug": slug_in, "cover_image": cover_image,
        "excerpt": excerpt, "body": body, "author": author, "status": status,
        "publish_date": publish_in, "seo_title": seo_title,
        "seo_description": seo_description, "og_image_url": og_image_url,
        "template": template_override, "featured": featured,
    }

    if errors:
        for e in errors:
            messages.error(request, e)
        return _blog_render_form(
            request, tenant, scope, post, form_data=form_data, errors=errors, status=400
        )

    is_new = post is None
    previously_featured = bool(post.featured) if post is not None else False
    if is_new:
        post = BlogPost(tenant=tenant)

    post.title = title
    post.slug = _unique_blog_slug(tenant, slug_in or title, instance=post)
    post.cover_image = cover_image
    post.excerpt = excerpt
    post.body = body
    post.author = author
    post.status = status
    post.seo_title = seo_title
    post.seo_description = seo_description
    post.og_image_url = og_image_url
    post.template = template_override

    # Stamp a publish date when first published, or honor an explicit one.
    if publish_date is not None:
        post.publish_date = publish_date
    elif status == BlogPost.STATUS_PUBLISHED and post.publish_date is None:
        post.publish_date = timezone.now()

    post.featured = featured
    if featured and not previously_featured:
        agg = tenant.blog_posts.aggregate(m=Max("featured_order"))
        post.featured_order = (agg["m"] or 0) + 1

    post.save()
    messages.success(request, f"Post “{post.title}” saved.")
    return redirect(_blog_nav_urls(scope, tenant)["list"])


def _blog_delete_post(request, tenant, scope, post_pk):
    post = get_object_or_404(BlogPost, pk=post_pk, tenant=tenant)
    title = post.title
    post.delete()
    messages.success(request, f"Post “{title}” deleted.")
    return redirect(_blog_nav_urls(scope, tenant)["list"])


def _blog_featured_toggle(request, tenant, scope, post_pk):
    post = get_object_or_404(BlogPost, pk=post_pk, tenant=tenant)
    post.featured = not post.featured
    if post.featured:
        agg = tenant.blog_posts.aggregate(m=Max("featured_order"))
        post.featured_order = (agg["m"] or 0) + 1
    post.save(update_fields=["featured", "featured_order", "updated_at"])
    return redirect(_blog_nav_urls(scope, tenant)["list"])


def _blog_reorder(request, tenant):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")
    order = payload.get("order")
    if not isinstance(order, list):
        return HttpResponseBadRequest("order must be a list")

    pks = []
    for raw in order:
        try:
            pks.append(int(raw))
        except (TypeError, ValueError):
            continue
    posts = {p.pk: p for p in tenant.blog_posts.filter(pk__in=pks)}
    for idx, pk in enumerate(pks):
        post = posts.get(pk)
        if post is not None:
            post.featured_order = idx
            post.save(update_fields=["featured_order", "updated_at"])
    return JsonResponse({"ok": True})


def _blog_settings_save(request, tenant, scope):
    template = (request.POST.get("template") or "").strip()
    if template not in BLOG_TEMPLATE_IDS:
        template = blog_render.DEFAULT_BLOG_TEMPLATE
    strip_style = (request.POST.get("strip_style") or "").strip()
    if strip_style not in BLOG_STRIP_IDS:
        strip_style = DEFAULT_BLOG_STRIP
    title = (request.POST.get("title") or "Blog").strip() or "Blog"
    heading = (request.POST.get("strip_heading") or "").strip() or "From the blog"
    strip_enabled = (request.POST.get("strip_enabled") or "") in ("on", "true", "1", "yes")
    try:
        strip_count = int(request.POST.get("strip_count") or 3)
    except (TypeError, ValueError):
        strip_count = 3
    strip_count = max(1, min(BLOG_STRIP_MAX, strip_count))

    tenant.blog_settings = {
        "template": template,
        "title": title[:120],
        "strip_enabled": strip_enabled,
        "strip_count": strip_count,
        "strip_heading": heading[:120],
        "strip_style": strip_style,
    }
    tenant.save(update_fields=["blog_settings", "updated_at"])
    messages.success(request, "Blog settings updated.")
    return redirect(_blog_nav_urls(scope, tenant)["list"])


# --------------------------------------------------------------------------- #
# Blog: agency surface (by pk)                                                #
# --------------------------------------------------------------------------- #


@agency_operator_required
def blog_list(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _blog_list(request, tenant, "agency")


@agency_operator_required
def blog_create(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _blog_form(request, tenant, "agency", None)


@agency_operator_required
def blog_edit(request, pk, post_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    post = get_object_or_404(BlogPost, pk=post_pk, tenant=tenant)
    return _blog_form(request, tenant, "agency", post)


@agency_operator_required
def blog_preview(request, pk, post_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    post = get_object_or_404(BlogPost, pk=post_pk, tenant=tenant)
    return _blog_preview(request, tenant, "agency", post)


@agency_operator_required
def blog_preview_new(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _blog_preview(request, tenant, "agency", None)


@agency_operator_required
@require_POST
def blog_delete(request, pk, post_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _blog_delete_post(request, tenant, "agency", post_pk)


@agency_operator_required
@require_POST
def blog_featured_toggle(request, pk, post_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _blog_featured_toggle(request, tenant, "agency", post_pk)


@agency_operator_required
@require_POST
def blog_reorder(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _blog_reorder(request, tenant)


@agency_operator_required
@require_POST
def blog_settings(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _blog_settings_save(request, tenant, "agency")


@agency_operator_required
@require_POST
def blog_sanitize(request, pk):
    get_object_or_404(Tenant, pk=pk)
    return _blog_sanitize(request)


@agency_operator_required
@require_GET
def blog_strip_preview(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _blog_strip_preview(request, tenant, "agency")


# --------------------------------------------------------------------------- #
# GHL Integrations                                                              #
# --------------------------------------------------------------------------- #


@agency_admin_required
def integrations(request):
    from collections import defaultdict
    agencies = list(GhlAgencyInstall.objects.all())
    installs = GhlInstall.objects.select_related("agency", "tenant").order_by("-installed_at")
    by_agency = defaultdict(list)
    orphan_installs = []
    for i in installs:
        if i.location_id.startswith("company:"):
            continue  # legacy placeholder, not a real sub-account
        if i.agency_id:
            by_agency[i.agency_id].append(i)
        else:
            orphan_installs.append(i)
    bound_location_ids = {
        i.location_id for lst in by_agency.values() for i in lst if i.tenant_id
    }
    agency_cards = [{"agency": a, "installs": by_agency.get(a.pk, [])} for a in agencies]
    tenants = Tenant.objects.order_by("name")
    connected_type = request.GET.get("connected")
    connected_label = ""
    if connected_type == "location":
        loc_id = request.GET.get("location_id", "")
        match = next((i for i in installs if i.location_id == loc_id), None)
        connected_label = (match.location_name if match else "") or loc_id
    return render(request, "dashboard/integrations.html", {
        "agency_cards": agency_cards,
        "orphan_installs": orphan_installs,
        "bound_location_ids": bound_location_ids,
        "tenants": tenants,
        "just_connected": connected_type in ("agency", "location"),
        "connected_type": connected_type,
        "connected_label": connected_label,
        "nav_section": "integrations",
    })


@agency_admin_required
@require_POST
def integrations_bind(request):
    agency = get_object_or_404(GhlAgencyInstall, pk=request.POST.get("agency_id"))
    location_id = (request.POST.get("location_id") or "").strip()
    tenant = get_object_or_404(Tenant, pk=request.POST.get("tenant_id"))
    next_url = request.POST.get("next", "")
    dest = next_url if (next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()})) else reverse("dashboard:integrations")
    if not any(loc.get("id") == location_id for loc in agency.available_locations):
        messages.error(request, "Unknown sub-account for that agency.")
        return redirect(dest)
    clash = (
        GhlInstall.objects.filter(location_id=location_id).exclude(tenant=tenant).exists()
        or Tenant.objects.filter(ghl_location_id=location_id).exclude(pk=tenant.pk).exists()
    )
    if clash:
        messages.error(request, "That sub-account is already linked to another site.")
        return redirect(dest)
    try:
        ghl_connect.bind_location(agency=agency, location_id=location_id, tenant=tenant)
        messages.success(request, f"Connected '{tenant.name}' to sub-account {location_id}.")
    except (ghl_oauth.TokenExchangeFailed, ValueError, ghl_crypto.TokenCryptoError, RuntimeError) as exc:
        messages.error(request, f"Could not connect: {exc}")
    except IntegrityError:
        messages.error(request, "That sub-account is already linked to another site.")
    return redirect(dest)


@agency_admin_required
@require_POST
def integrations_bind_orphan(request):
    install = get_object_or_404(GhlInstall, pk=request.POST.get("install_id"))
    tenant = get_object_or_404(Tenant, pk=request.POST.get("tenant_id"))
    if install.agency_id:
        messages.error(request, "This install belongs to an agency. Use the agency bind flow instead.")
        return redirect("dashboard:integrations")
    clash = (
        Tenant.objects.filter(ghl_location_id=install.location_id).exclude(pk=tenant.pk).exists()
        or (install.tenant_id and install.tenant_id != tenant.pk)
    )
    if clash:
        messages.error(request, "That sub-account is already linked to another site.")
        return redirect("dashboard:integrations")
    try:
        ghl_connect.bind_orphan_install(install=install, tenant=tenant)
        messages.success(request, f"Connected '{tenant.name}' to sub-account {install.location_id}.")
    except IntegrityError:
        messages.error(request, "That sub-account is already linked to another site.")
    return redirect("dashboard:integrations")


@agency_admin_required
@require_POST
def integrations_reconnect(request):
    install = get_object_or_404(GhlInstall, pk=request.POST.get("install_id"))
    try:
        ghl_connect.reconnect_install(install)
        messages.success(request, f"Reconnected {install.location_id}.")
    except (ghl_oauth.TokenExchangeFailed, ValueError, ghl_crypto.TokenCryptoError, RuntimeError) as exc:
        messages.error(request, f"Reconnect failed: {exc}")
    return redirect("dashboard:integrations")


@agency_admin_required
@require_POST
def integrations_disconnect(request):
    install = get_object_or_404(GhlInstall, pk=request.POST.get("install_id"))
    install.status = GhlInstall.STATUS_DISCONNECTED
    install.save(update_fields=["status", "updated_at"])
    if install.tenant and install.tenant.ghl_location_id == install.location_id:
        install.tenant.ghl_location_id = None
        install.tenant.save(update_fields=["ghl_location_id", "updated_at"])
    messages.success(request, f"Disconnected {install.location_id}.")
    return redirect("dashboard:integrations")


@agency_admin_required
@require_POST
def integrations_refresh_locations(request):
    agency = get_object_or_404(GhlAgencyInstall, pk=request.POST.get("agency_id"))
    app_id = (settings.GHL_CLIENT_ID or "").split("-")[0]
    try:
        token = ghl_connect.ensure_fresh_agency_token(agency)
        agency.available_locations = ghl_oauth.list_installed_locations(
            agency_access_token=token, company_id=agency.company_id, app_id=app_id
        )
        agency.company_name = ghl_oauth.fetch_company_name(
            agency_access_token=token, company_id=agency.company_id
        ) or agency.company_name
        agency.save(update_fields=["available_locations", "company_name", "updated_at"])
        messages.success(request, "Sub-account list refreshed.")
    except (ghl_oauth.TokenExchangeFailed, ghl_crypto.TokenCryptoError, RuntimeError) as exc:
        messages.error(request, f"Refresh failed: {exc}")
    return redirect("dashboard:integrations")


@agency_admin_required
@require_POST
def integrations_disconnect_agency(request):
    agency = get_object_or_404(GhlAgencyInstall, pk=request.POST.get("agency_id"))
    for inst in agency.location_installs.all():
        if inst.tenant and inst.tenant.ghl_location_id == inst.location_id:
            inst.tenant.ghl_location_id = None
            inst.tenant.save(update_fields=["ghl_location_id", "updated_at"])
    agency.location_installs.all().delete()
    label = agency.company_name or agency.company_id
    agency.delete()
    messages.success(request, f"Disconnected agency {label}.")
    return redirect("dashboard:integrations")


@agency_admin_required
@require_POST
def integrations_rename_agency(request):
    agency = get_object_or_404(GhlAgencyInstall, pk=request.POST.get("agency_id"))
    agency.company_name = (request.POST.get("company_name") or "").strip()
    agency.save(update_fields=["company_name", "updated_at"])
    messages.success(request, "Agency name updated.")
    return redirect("dashboard:integrations")


# --------------------------------------------------------------------------- #
# Blog: tenant surface (host resolves to a tenant)                            #
# --------------------------------------------------------------------------- #


@tenant_member_required
def blog_list_self(request):
    return _blog_list(request, request.tenant, "tenant")


@tenant_member_required
def blog_create_self(request):
    return _blog_form(request, request.tenant, "tenant", None)


@tenant_member_required
def blog_edit_self(request, post_pk):
    post = get_object_or_404(BlogPost, pk=post_pk, tenant=request.tenant)
    return _blog_form(request, request.tenant, "tenant", post)


@tenant_member_required
def blog_preview_self(request, post_pk):
    post = get_object_or_404(BlogPost, pk=post_pk, tenant=request.tenant)
    return _blog_preview(request, request.tenant, "tenant", post)


@tenant_member_required
def blog_preview_new_self(request):
    return _blog_preview(request, request.tenant, "tenant", None)


@tenant_member_required
@require_POST
def blog_delete_self(request, post_pk):
    return _blog_delete_post(request, request.tenant, "tenant", post_pk)


@tenant_member_required
@require_POST
def blog_featured_toggle_self(request, post_pk):
    return _blog_featured_toggle(request, request.tenant, "tenant", post_pk)


@tenant_member_required
@require_POST
def blog_reorder_self(request):
    return _blog_reorder(request, request.tenant)


@tenant_member_required
@require_POST
def blog_settings_self(request):
    return _blog_settings_save(request, request.tenant, "tenant")


@tenant_member_required
@require_POST
def blog_sanitize_self(request):
    return _blog_sanitize(request)


@tenant_member_required
@require_GET
def blog_strip_preview_self(request):
    return _blog_strip_preview(request, request.tenant, "tenant")
