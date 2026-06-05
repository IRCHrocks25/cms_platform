import json
import logging
import re
import secrets
import threading
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.views.decorators.http import require_POST, require_GET

import httpx

from core.models import (
    CustomDomain, Template, Tenant, TenantMembership, MediaAsset, ContentVersion,
    BlogPost, BLOG_TEMPLATE_CHOICES, BLOG_TEMPLATE_IDS,
    BLOG_STRIP_CHOICES, BLOG_STRIP_IDS, DEFAULT_BLOG_STRIP, _unique_blog_slug,
    Page, RESERVED_PAGE_SLUGS, AnnotationJob,
)
from core.permissions import agency_operator_required, tenant_member_required
from core.renderer import render_site, merge_with_defaults
from core.parser import build_schema
from core.services import cloudflare as cloudflare_service
from core.services import blog_render
from core.services import cloudinary_media
from core.services.annotator import annotate_html, AnnotatorError
from core.services.sanitizer import sanitize_html, strip_to_text
from core.urls_helpers import build_tenant_url_bundle


User = get_user_model()
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #


STARTER_TEMPLATE_HTML = """\
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>
  <div data-edit="hero.body" data-type="richtext" data-label="Body">
    <p>Tell visitors what you do.</p>
  </div>
  <img data-edit="hero.image" data-type="image" data-label="Photo"
       src="https://placehold.co/800x400" alt="">
  <a data-edit="hero.cta" data-type="link" data-label="Button" href="#">Learn more</a>
</section>

<style data-tokens>
  :root {
    --primary: #2563eb;
    --bg: #ffffff;
  }
</style>
"""


GA_ID_RE = re.compile(r"^(G-[A-Za-z0-9]+|UA-\d+-\d+)$")
SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
)
PASSWORD_ALPHABET = (
    "abcdefghjkmnpqrstuvwxyz"
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "23456789"
)
SESSION_CREDS_KEY = "agency_one_time_creds"
CREDS_TTL_MINUTES = 10


# --------------------------------------------------------------------------- #
# Dispatcher                                                                   #
# --------------------------------------------------------------------------- #


def dashboard_root(request):
    """`/dashboard/` — branches on whether the host resolved to a tenant."""
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
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        html_source = request.POST.get("html_source") or ""

        if not name or not html_source.strip():
            messages.error(request, "Name and HTML source are required.")
            return render(
                request,
                "dashboard/template_form.html",
                {"form_data": request.POST},
            )

        template = Template.objects.create(
            name=name,
            description=description,
            html_source=html_source,
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
            },
        },
    )


@agency_operator_required
def template_detail(request, pk):
    template = get_object_or_404(Template, pk=pk)

    if request.method == "POST":
        template.name = (request.POST.get("name") or template.name).strip()
        template.description = (request.POST.get("description") or "").strip()
        template.html_source = request.POST.get("html_source") or template.html_source
        template.save()
        messages.success(request, "Template updated.")
        return redirect("dashboard:template_detail", pk=template.pk)

    return render(
        request,
        "dashboard/template_form.html",
        {
            "template": template,
            "form_data": {"name": "", "description": "", "html_source": ""},
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


# How long a job may sit non-terminal before the status endpoint declares it
# stale and fails it. The worker thread itself is bounded by OPENAI_TIMEOUT
# (~120s); this only catches a worker that DIED (process restart) and left a row
# stuck "running" forever. Comfortably above the 180s Gunicorn worker budget.
ANNOTATION_JOB_STALE_SECONDS = 300


def _run_annotation_job(job_id, raw_html):
    """Worker body (runs in a background thread, NOT a web request).

    Has no request/proxy timeout, so the OpenAI call may take as long as
    settings.OPENAI_TIMEOUT. Writes the outcome back onto the AnnotationJob row.
    Must never raise out of the thread — any escape is logged and recorded as an
    error status so the poller stops cleanly instead of hanging forever.
    """
    from django.db import connection

    AnnotationJob.objects.filter(id=job_id).update(status=AnnotationJob.STATUS_RUNNING)
    try:
        annotated = annotate_html(raw_html)
        schema = build_schema(annotated)
        sections_summary = [
            {"id": s["id"], "label": s["label"], "field_count": len(s.get("fields", []))}
            for s in schema.get("sections", [])
        ]
        AnnotationJob.objects.filter(id=job_id).update(
            status=AnnotationJob.STATUS_DONE,
            result_html=annotated,
            sections=sections_summary,
        )
    except AnnotatorError as exc:
        AnnotationJob.objects.filter(id=job_id).update(
            status=AnnotationJob.STATUS_ERROR, error=str(exc)
        )
    except Exception as exc:  # noqa: BLE001 — background thread must never crash silently
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
    in the template dropdown — the Template is created inside the same
    transaction so a partial failure leaks nothing.
    """
    templates = Template.objects.all().order_by("name")

    form_data = {
        "name": "",
        "subdomain": "",
        "template": "",
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

    if not name:
        errors.append("Site name is required.")

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
        if not new_template_name:
            errors.append("New template name is required.")
        if not new_template_html.strip():
            errors.append("New template HTML is required.")
    else:
        try:
            template = Template.objects.get(pk=template_id)
        except (Template.DoesNotExist, ValueError):
            errors.append("That template no longer exists.")

    if not client_username:
        errors.append("Client username is required.")
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

    password = _generate_password()

    try:
        with transaction.atomic():
            if inline_new_template:
                template = Template.objects.create(
                    name=new_template_name,
                    description=new_template_description,
                    html_source=new_template_html,
                )

            user = User.objects.create_user(
                username=client_username,
                email=client_email,
                password=password,
            )
            user.is_active = True
            user.is_staff = False
            user.save(update_fields=["is_active", "is_staff"])

            tenant = Tenant.objects.create(
                name=name,
                subdomain=subdomain,
                custom_domain=custom_domain,
                template=template,
                owner=user,
                content=template.schema.get("defaults", {}) or {},
                is_published=True,
            )
            TenantMembership.objects.create(
                tenant=tenant,
                user=user,
                role=TenantMembership.ROLE_OWNER,
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

    token = _stash_credentials_in_session(request, user, password)
    return redirect(
        f"{reverse('dashboard:site_created', args=[tenant.pk])}?token={token}"
    )


@agency_operator_required
@require_GET
def check_subdomain(request):
    """JSON endpoint: GET /dashboard/sites/check-subdomain/?value=..."""
    value = (request.GET.get("value") or "").strip()
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


def _generate_password():
    return get_random_string(length=16, allowed_chars=PASSWORD_ALPHABET)


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


def _pop_credentials_from_session(request, token):
    """Return the credentials dict and remove it. Returns None if missing/expired."""
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
    return payload


@agency_operator_required
@require_GET
def site_created(request, pk):
    """
    Post-create / post-reveal landing for a tenant site.

    Merges the one-time credentials reveal with the shareable URL bundle
    so the operator gets everything in one shot. Without a fresh `?token=`,
    the credentials block is omitted and only the URL panel renders — the
    page is then safe to bookmark/refresh.
    """
    tenant = get_object_or_404(Tenant, pk=pk)
    token = request.GET.get("token") or ""
    payload = _pop_credentials_from_session(request, token) if token else None
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
    payload = _pop_credentials_from_session(request, token) if token else None

    return render(
        request,
        "dashboard/credentials.html",
        {
            "context_label": "user",
            "credentials_user": user,
            "payload": payload,
            "login_url": None,
            "back_url": reverse("dashboard:user_detail", args=[user.pk]),
            "back_label": "Done — back to user",
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
    custom_domain = tenant.custom_domains.order_by("-created_at").first()
    member_user_ids = list(members.values_list("user_id", flat=True))
    add_member_candidates = (
        User.objects.exclude(pk__in=member_user_ids)
        .filter(is_active=True)
        .order_by("username")[:200]
    )
    activity = (
        tenant.versions.select_related("saved_by").order_by("-saved_at")[:20]
    )
    return render(
        request,
        "dashboard/tenant_detail.html",
        {
            "tenant": tenant,
            "members": members,
            "add_member_candidates": add_member_candidates,
            "activity": activity,
            "custom_domain": custom_domain,
            "nav_section": "sites",
            "role_choices": TenantMembership.ROLE_CHOICES,
            # URLs for visiting the client's live site (subdomain host) and a
            # fallback that always works on the current host (/site/<sub>/).
            "site_urls": build_tenant_url_bundle(request, tenant),
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
    tenant.save(update_fields=["name", "subdomain", "updated_at"])
    messages.success(request, "Site settings updated.")
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
    tenant.delete()
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
def tenant_member_remove(request, pk, membership_id):
    tenant = get_object_or_404(Tenant, pk=pk)
    membership = get_object_or_404(
        TenantMembership, pk=membership_id, tenant=tenant
    )
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
            "nav_section": "users",
        },
    )


@agency_operator_required
@require_POST
def user_reset_password(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    password = _generate_password()
    user_obj.set_password(password)
    user_obj.save(update_fields=["password"])
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
    user_obj.is_active = False
    user_obj.save(update_fields=["is_active"])
    messages.success(request, f"Deactivated {user_obj.username}.")
    return redirect("dashboard:user_detail", pk=user_obj.pk)


@agency_operator_required
@require_POST
def user_activate(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
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
def tenant_video_sign(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _video_sign(request, tenant)


@agency_operator_required
@require_POST
def tenant_video_confirm(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _video_confirm(request, tenant)


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
def tenant_video_sign_self(request):
    return _video_sign(request, request.tenant)


@tenant_member_required
@require_POST
def tenant_video_confirm_self(request):
    return _video_confirm(request, request.tenant)


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
            "home": reverse("dashboard:tenant_home"),
            "blog": reverse("dashboard:blog_list_self"),
        }
    return {
        "list": reverse("dashboard:page_list", args=[tenant.pk]),
        "new": reverse("dashboard:page_create", args=[tenant.pk]),
        "home": reverse("dashboard:tenant_editor", args=[tenant.pk]),
        "blog": reverse("dashboard:blog_list", args=[tenant.pk]),
    }


def _page_row_urls(scope, tenant, page):
    if scope == "tenant":
        return {
            "edit": reverse("dashboard:page_editor_self", args=[page.pk]),
            "publish": reverse("dashboard:page_publish_self", args=[page.pk]),
            "delete": reverse("dashboard:page_delete_self", args=[page.pk]),
            "live": f"/{page.slug}/",
        }
    return {
        "edit": reverse("dashboard:page_editor", args=[tenant.pk, page.pk]),
        "publish": reverse("dashboard:page_publish", args=[tenant.pk, page.pk]),
        "delete": reverse("dashboard:page_delete", args=[tenant.pk, page.pk]),
        "live": f"/site/{tenant.subdomain}/{page.slug}/",
    }


def _page_list(request, tenant, scope):
    pages = [
        {"obj": p, "urls": _page_row_urls(scope, tenant, p)}
        for p in tenant.pages.all()
    ]
    return render(
        request,
        "dashboard/page_list.html",
        {
            "tenant": tenant,
            "scope": scope,
            "pages": pages,
            "nav_urls": _page_nav_urls(scope, tenant),
            "templates": Template.objects.order_by("name"),
            "reserved_slugs": ", ".join(sorted(RESERVED_PAGE_SLUGS)),
        },
    )


def _page_create(request, tenant, scope):
    nav = _page_nav_urls(scope, tenant)
    title = (request.POST.get("title") or "").strip()
    slug = slugify(request.POST.get("slug") or title)[:80]
    template = Template.objects.filter(pk=request.POST.get("template")).first()

    errors = []
    if not title:
        errors.append("A page title is required.")
    if not slug:
        errors.append("A URL slug is required.")
    elif slug in RESERVED_PAGE_SLUGS:
        errors.append(f"'/{slug}/' is reserved — choose a different slug.")
    elif tenant.pages.filter(slug=slug).exists():
        errors.append(f"This site already has a page at /{slug}/.")
    if template is None:
        errors.append("Choose a template for the page.")

    if errors:
        for e in errors:
            messages.error(request, e)
        return redirect(nav["list"])

    page = Page.objects.create(tenant=tenant, template=template, title=title, slug=slug)
    messages.success(request, f"Page “{page.title}” created — start editing.")
    if scope == "tenant":
        return redirect("dashboard:page_editor_self", page_pk=page.pk)
    return redirect("dashboard:page_editor", pk=tenant.pk, page_pk=page.pk)


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
    return _page_create(request, get_object_or_404(Tenant, pk=pk), "agency")


@agency_operator_required
def page_editor(request, pk, page_pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    page = _get_tenant_page(tenant, page_pk)
    return _render_editor(request, tenant, scope="agency", page=page)


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
    return _page_create(request, request.tenant, "tenant")


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
    return _page_delete(request, request.tenant, "tenant", page_pk)


# --------------------------------------------------------------------------- #
# Shared helpers                                                               #
# --------------------------------------------------------------------------- #


def _render_editor(request, tenant, *, scope, page=None):
    # The editor drives either the tenant home (page=None) or one inner Page.
    # Both expose the same template / content / is_published shape, so the only
    # differences are the action URLs and the bar labels.
    editable = page or tenant
    schema = editable.template.schema or {"sections": []}
    content = merge_with_defaults(schema, editable.content)

    sections = schema.get("sections", [])
    # Brand tokens (global colors) and the header navigation are conceptually
    # distinct from per-section content edits, so the editor surfaces each on its
    # own tab ("Brand" / "Navigation"). Everything else is "Content".
    brand_section = next((s for s in sections if s.get("id") == "brand"), None)
    # The "Navigation" tab gathers the site chrome — both the header nav and the
    # footer — so they live apart from the page-body content sections.
    nav_groups = {"header", "footer"}
    nav_sections = [
        s for s in sections
        if s.get("id") != "brand" and (s.get("group") or "").lower() in nav_groups
    ]
    nav_ids = {s.get("id") for s in nav_sections}
    # Within the Navigation tab, split into Header / Footer sub-tabs.
    header_sections = [s for s in nav_sections if (s.get("group") or "").lower() == "header"]
    footer_sections = [s for s in nav_sections if (s.get("group") or "").lower() == "footer"]
    content_sections = [
        s for s in sections
        if s.get("id") != "brand" and s.get("id") not in nav_ids
    ]

    grouped: dict[str, list] = {}
    for section in content_sections:
        grouped.setdefault(section.get("group", "Sections"), []).append(section)

    # Layout mode is driven by how many entries land in the section nav.
    layout_mode = "compact" if len(content_sections) <= 6 else (
        "standard" if len(content_sections) <= 15 else "dense"
    )

    # Image/video uploads create per-tenant MediaAssets (page-independent), so
    # the page editor reuses the tenant-scoped upload/video endpoints. Version
    # history is home-only for now — pages pass empty version URLs and the
    # editor hides the History button (see editor.html).
    if scope == "tenant":
        upload_url = reverse("dashboard:tenant_upload_self")
        video_sign_url = reverse("dashboard:tenant_video_sign_self")
        video_confirm_url = reverse("dashboard:tenant_video_confirm_self")
        settings_url = reverse("dashboard:tenant_site_settings_self")
        blog_url = reverse("dashboard:blog_list_self")
        page_list_url = reverse("dashboard:page_list_self")
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
            versions_url = version_restore_url = ""
            live_url = f"/{page.slug}/"
    else:
        upload_url = reverse("dashboard:tenant_upload", args=[tenant.pk])
        video_sign_url = reverse("dashboard:tenant_video_sign", args=[tenant.pk])
        video_confirm_url = reverse("dashboard:tenant_video_confirm", args=[tenant.pk])
        settings_url = reverse("dashboard:tenant_site_settings", args=[tenant.pk])
        blog_url = reverse("dashboard:blog_list", args=[tenant.pk])
        page_list_url = reverse("dashboard:page_list", args=[tenant.pk])
        if page is None:
            preview_url = reverse("dashboard:tenant_preview", args=[tenant.pk])
            save_url = reverse("dashboard:tenant_save", args=[tenant.pk])
            publish_url = reverse("dashboard:tenant_publish", args=[tenant.pk])
            versions_url = reverse("dashboard:tenant_versions", args=[tenant.pk])
            version_restore_url = reverse("dashboard:tenant_version_restore", args=[tenant.pk])
            live_url = f"/site/{tenant.subdomain}/"
        else:
            preview_url = reverse("dashboard:page_preview", args=[tenant.pk, page.pk])
            save_url = reverse("dashboard:page_save", args=[tenant.pk, page.pk])
            publish_url = reverse("dashboard:page_publish", args=[tenant.pk, page.pk])
            versions_url = version_restore_url = ""
            live_url = f"/site/{tenant.subdomain}/{page.slug}/"

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

    return render(
        request,
        "dashboard/editor.html",
        {
            "tenant": tenant,
            "editing_page": page,
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
            "link_targets": link_targets,
            "grouped_sections": grouped,
            "content_json": json.dumps(content),
            "layout_mode": layout_mode,
            "preview_url": preview_url,
            "save_url": save_url,
            "upload_url": upload_url,
            "video_sign_url": video_sign_url,
            "video_confirm_url": video_confirm_url,
            "versions_url": versions_url,
            "version_restore_url": version_restore_url,
            "publish_url": publish_url,
            "settings_url": settings_url,
            "blog_url": blog_url,
            "page_list_url": page_list_url,
            "live_url": live_url,
            "scope": scope,
        },
    )


def _render_preview(editable):
    content = merge_with_defaults(editable.template.schema, editable.content)
    html = render_site(editable.template.html_source, content, preview=True)
    return HttpResponse(html)


def _save_content(request, editable):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    content = payload.get("content")
    if not isinstance(content, dict):
        return HttpResponseBadRequest("content must be an object")

    # Version history is the tenant home's rolling-10 snapshots. Inner pages
    # don't have undo yet (backlog), so only snapshot when editing the home.
    if isinstance(editable, Tenant):
        ContentVersion.objects.create(
            tenant=editable,
            snapshot=editable.content,
            saved_by=request.user,
        )
        keep_ids = list(
            editable.versions.values_list("id", flat=True).order_by("-saved_at")[:10]
        )
        editable.versions.exclude(id__in=keep_ids).delete()

    editable.content = content
    editable.save(update_fields=["content", "updated_at"])

    return JsonResponse({"ok": True, "updated_at": editable.updated_at.isoformat()})


# --------------------------------------------------------------------------- #
# Version history / undo                                                        #
# --------------------------------------------------------------------------- #


def _versions_list(tenant, scope):
    items = []
    for v in tenant.versions.select_related("saved_by").order_by("-saved_at")[:10]:
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

    version = tenant.versions.filter(id=payload.get("version_id")).first()
    if version is None:
        return JsonResponse({"ok": False, "error": "That version no longer exists."}, status=404)

    restored = version.snapshot
    # Snapshot the CURRENT content first, so the restore is itself undoable.
    ContentVersion.objects.create(
        tenant=tenant, snapshot=tenant.content, saved_by=request.user
    )
    keep_ids = list(
        tenant.versions.values_list("id", flat=True).order_by("-saved_at")[:10]
    )
    tenant.versions.exclude(id__in=keep_ids).delete()

    tenant.content = restored
    tenant.save(update_fields=["content", "updated_at"])
    return JsonResponse({"ok": True})


def _version_preview(tenant, version_id):
    version = get_object_or_404(tenant.versions, id=version_id)
    content = merge_with_defaults(tenant.template.schema, version.snapshot)
    html = render_site(tenant.template.html_source, content, preview=False)
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


def _save_upload(request, tenant):
    """Image upload: validated at the door, then stored on Cloudinary and
    served with f_auto,q_auto. Returns a clear error the editor can display."""
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"ok": False, "error": "No file received."}, status=400)

    ok, error = cloudinary_media.validate_image(upload)
    if not ok:
        return JsonResponse({"ok": False, "error": error}, status=400)

    if not cloudinary_media.is_configured():
        return JsonResponse(
            {"ok": False, "error": "Image storage isn't configured."}, status=500
        )

    try:
        result = cloudinary_media.upload_image(upload, tenant)
    except Exception:
        logger.exception("Cloudinary image upload failed for tenant %s", tenant.pk)
        return JsonResponse(
            {"ok": False, "error": "Upload failed — please try again."}, status=502
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


def _video_sign(request, tenant):
    """Return signed params for a direct browser->Cloudinary video upload."""
    if not cloudinary_media.is_configured():
        return JsonResponse(
            {"ok": False, "error": "Video storage isn't configured."}, status=500
        )
    return JsonResponse({"ok": True, **cloudinary_media.sign_video_upload(tenant)})


def _video_confirm(request, tenant):
    """Verify a directly-uploaded video (resource_type, size, duration) and store it."""
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    public_id = (payload.get("public_id") or "").strip()
    if not public_id:
        return JsonResponse({"ok": False, "error": "Missing video reference."}, status=400)

    info, error = cloudinary_media.verify_video(public_id)
    if error:
        return JsonResponse({"ok": False, "error": error}, status=400)

    secure_url = info.get("secure_url", "")
    asset = MediaAsset.objects.create(
        tenant=tenant,
        original_name=(payload.get("original_name") or "")[:240],
        resource_type=MediaAsset.RESOURCE_VIDEO,
        public_id=public_id,
        secure_url=secure_url,
        bytes=info.get("bytes", 0),
    )
    return JsonResponse({"ok": True, "url": secure_url, "id": asset.id})


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
# Custom domain (Cloudflare for SaaS) — agency surface                         #
# --------------------------------------------------------------------------- #


SSL_STATUS_MESSAGES = {
    "pending_validation": "Waiting on the _acme-challenge CNAME — make sure both CNAMEs are at your registrar, then check again.",
    "pending_issuance": "Validation passed. Cloudflare is issuing the SSL certificate — try again in a moment.",
    "pending_deployment": "Certificate issued — Cloudflare is deploying it to the edge. Try again in a moment.",
    "initializing": "Just created. Try again in a moment.",
}


def _ssl_status_message(hostname_status: str, ssl_status: str) -> str:
    if ssl_status in SSL_STATUS_MESSAGES:
        return SSL_STATUS_MESSAGES[ssl_status]
    parts = []
    if hostname_status:
        parts.append(f"hostname: {hostname_status}")
    if ssl_status:
        parts.append(f"ssl: {ssl_status}")
    detail = ", ".join(parts) or "unknown"
    return f"Not active yet — Cloudflare reports {detail}. DNS can take a few minutes."


def _dns_name_for_domain(domain: str) -> str:
    """`@` for a root domain (2 labels), else the leftmost label."""
    cleaned = (domain or "").strip().rstrip(".")
    if not cleaned:
        return "@"
    labels = cleaned.split(".")
    if len(labels) <= 2:
        return "@"
    return labels[0]


def _render_custom_domain_partial(request, tenant, *, error=None, info=None):
    custom_domain = tenant.custom_domains.order_by("-created_at").first()
    dns_name = _dns_name_for_domain(custom_domain.domain) if custom_domain else None
    if dns_name == "@":
        acme_challenge_name = "_acme-challenge"
    elif dns_name:
        acme_challenge_name = f"_acme-challenge.{dns_name}"
    else:
        acme_challenge_name = None
    dcv_suffix = settings.CLOUDFLARE_DCV_DELEGATION_TARGET
    dcv_target = f"{custom_domain.domain}.{dcv_suffix}" if custom_domain else None
    return render(
        request,
        "dashboard/partials/custom_domain.html",
        {
            "tenant": tenant,
            "custom_domain": custom_domain,
            "dns_name": dns_name,
            "acme_challenge_name": acme_challenge_name,
            "dcv_target": dcv_target,
            "dcv_target_suffix": dcv_suffix,
            "error": error,
            "info": info,
        },
    )


@agency_operator_required
@require_GET
def tenant_custom_domain_section(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _render_custom_domain_partial(request, tenant)


@agency_operator_required
@require_POST
def tenant_custom_domain_add(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    domain = (request.POST.get("domain") or "").strip().lower().rstrip(".")

    if not domain:
        return _render_custom_domain_partial(
            request, tenant, error="Enter a domain to add."
        )
    if not DOMAIN_RE.match(domain):
        return _render_custom_domain_partial(
            request, tenant,
            error="That doesn't look like a valid domain (e.g. training.acme.com).",
        )
    if CustomDomain.objects.filter(domain=domain).exists():
        return _render_custom_domain_partial(
            request, tenant, error=f"“{domain}” is already in use."
        )

    try:
        cf_response = cloudflare_service.add_custom_hostname(domain)
    except httpx.HTTPError as exc:
        return _render_custom_domain_partial(
            request, tenant,
            error=f"Cloudflare rejected that domain ({exc.__class__.__name__}). Try again or contact support.",
        )
    except Exception:
        return _render_custom_domain_partial(
            request, tenant,
            error="Couldn't reach Cloudflare. Try again in a moment.",
        )

    result = cf_response.get("result") or {}
    CustomDomain.objects.create(
        tenant=tenant,
        domain=domain,
        cloudflare_hostname_id=result.get("id") or "",
        is_verified=False,
    )
    return _render_custom_domain_partial(request, tenant)


@agency_operator_required
@require_POST
def tenant_custom_domain_verify(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    custom_domain = tenant.custom_domains.order_by("-created_at").first()
    if custom_domain is None:
        return _render_custom_domain_partial(
            request, tenant, error="No domain to verify."
        )
    if not custom_domain.cloudflare_hostname_id:
        return _render_custom_domain_partial(
            request, tenant,
            error="This domain has no Cloudflare hostname id — recreate it.",
        )

    try:
        data = cloudflare_service.get_hostname_status(
            custom_domain.cloudflare_hostname_id
        )
    except httpx.HTTPError as exc:
        return _render_custom_domain_partial(
            request, tenant,
            error=f"Couldn't check status with Cloudflare ({exc.__class__.__name__}).",
        )
    except Exception:
        return _render_custom_domain_partial(
            request, tenant,
            error="Couldn't reach Cloudflare. Try again in a moment.",
        )

    result = data.get("result") or {}
    ssl_data = result.get("ssl") or {}
    hostname_status = result.get("status") or ""
    ssl_status = ssl_data.get("status") or ""

    is_fully_active = hostname_status == "active" and ssl_status == "active"
    if is_fully_active and not custom_domain.is_verified:
        custom_domain.is_verified = True
        custom_domain.save(update_fields=["is_verified", "updated_at"])

    if is_fully_active:
        return _render_custom_domain_partial(request, tenant)

    return _render_custom_domain_partial(
        request, tenant,
        info=_ssl_status_message(hostname_status, ssl_status),
    )


@agency_operator_required
@require_POST
def tenant_custom_domain_delete(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    custom_domain = tenant.custom_domains.order_by("-created_at").first()
    if custom_domain is None:
        return _render_custom_domain_partial(request, tenant)

    if custom_domain.cloudflare_hostname_id:
        try:
            cloudflare_service.delete_custom_hostname(
                custom_domain.cloudflare_hostname_id
            )
        except httpx.HTTPError as exc:
            return _render_custom_domain_partial(
                request, tenant,
                error=f"Couldn't remove from Cloudflare ({exc.__class__.__name__}). Domain not deleted.",
            )
        except Exception:
            return _render_custom_domain_partial(
                request, tenant,
                error="Couldn't reach Cloudflare. Domain not deleted.",
            )

    custom_domain.delete()
    return _render_custom_domain_partial(request, tenant)


# --------------------------------------------------------------------------- #
# Custom domain — agency-wide list + override actions                          #
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
    domain = get_object_or_404(CustomDomain, pk=pk)
    if not domain.is_verified:
        domain.is_verified = True
        domain.save(update_fields=["is_verified", "updated_at"])
        messages.success(request, f"“{domain.domain}” force-marked as verified.")
    else:
        messages.info(request, f"“{domain.domain}” was already verified.")
    return redirect("dashboard:custom_domain_list")


@agency_operator_required
@require_POST
def custom_domain_force_delete_local(request, pk):
    domain = get_object_or_404(CustomDomain, pk=pk)
    label = domain.domain
    domain.delete()
    messages.success(
        request,
        f"“{label}” deleted locally. Cloudflare was not touched — clean up the CF hostname manually if needed.",
    )
    return redirect("dashboard:custom_domain_list")


# --------------------------------------------------------------------------- #
# Blog — shared helpers (two surfaces: agency by pk, tenant by host)           #
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
# Blog — agency surface (by pk)                                                #
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
# Blog — tenant surface (host resolves to a tenant)                            #
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
