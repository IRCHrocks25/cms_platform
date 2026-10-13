"""Public, host-scoped native Sites form submission endpoint."""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import BusinessFormBinding, Page, Tenant
from core.services import blocks
from core.services.business_forms import queue_submission, read_form_token


def _same_origin(request) -> bool:
    raw = request.headers.get("Origin", "")
    if not raw:
        return False
    parsed = urlsplit(raw)
    return (
        parsed.scheme == request.scheme
        and parsed.netloc.lower() == request.get_host().lower()
        and not parsed.path and not parsed.query and not parsed.fragment
    )


def _published_form(tenant, page, form_id: str) -> bool:
    if not tenant.is_published or (page and not page.is_published):
        return False
    template = page.template if page else tenant.template
    content = page.content if page else tenant.content
    html = blocks.render_content(template, content, preview=False, site_settings=tenant.site_settings or {})
    soup = BeautifulSoup(html, "lxml")
    return soup.find(attrs={"data-cms-form": form_id}) is not None


def _rate_limited(tenant_id, form_id, address) -> bool:
    key = "site-form:" + hashlib.sha256(f"{tenant_id}:{form_id}:{address}".encode()).hexdigest()
    cache.add(key, 0, timeout=600)
    try:
        return cache.incr(key) > 10
    except ValueError:
        return False


@csrf_exempt  # Signed page/form token plus exact Origin replace the CSRF cookie.
@require_POST
def submit_business_form(request, subdomain=None):
    if not getattr(settings, "SITES_BUSINESS_FORMS_ENABLED", False):
        return JsonResponse({"error": "Form capture unavailable"}, status=503)
    tenant = getattr(request, "tenant", None)
    if subdomain is not None and tenant is None:
        tenant = Tenant.objects.filter(subdomain=subdomain).first()
    if subdomain is not None and tenant is not None and tenant.subdomain != subdomain:
        return JsonResponse({"error": "Site not found"}, status=404)
    if tenant is None:
        return JsonResponse({"error": "Site not found"}, status=404)
    if not _same_origin(request):
        return JsonResponse({"error": "Invalid origin"}, status=403)
    if len(request.body) > 20_000:
        return JsonResponse({"error": "Form too large"}, status=413)
    forbidden = {
        "tenant", "tenant_id", "business", "business_id",
        "organization", "organization_id", "org", "org_id", "receiver_url",
    }
    if forbidden.intersection(request.POST):
        return JsonResponse({"error": "Routing fields are not accepted"}, status=400)
    if request.POST.get("website"):
        return JsonResponse({"error": "Invalid form"}, status=400)
    try:
        claim = read_form_token(request.POST.get("form_token", ""), tenant)
    except (signing.BadSignature, signing.SignatureExpired, TypeError, ValueError):
        return JsonResponse({"error": "Invalid form token"}, status=400)
    page = None
    if claim.get("page") is not None:
        page = Page.objects.filter(pk=claim["page"], tenant=tenant).select_related("template").first()
        if page is None:
            return JsonResponse({"error": "Page not found"}, status=404)
    form_id = claim["form"]
    if not _published_form(tenant, page, form_id):
        return JsonResponse({"error": "Form not published"}, status=404)
    if not BusinessFormBinding.objects.filter(tenant=tenant).exists():
        return JsonResponse({"error": "Form capture unavailable"}, status=503)
    name = request.POST.get("name", "").strip()
    email = request.POST.get("email", "").strip().lower()
    phone = request.POST.get("phone", "").strip()
    message = request.POST.get("message", "").strip()
    if len(name) > 200 or len(email) > 254 or len(phone) > 40 or len(message) > 5000:
        return JsonResponse({"error": "Form field too long"}, status=400)
    if not email and not phone:
        return JsonResponse({"error": "Email or phone required"}, status=400)
    if email:
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({"error": "Invalid email"}, status=400)
    if _rate_limited(tenant.pk, form_id, request.META.get("REMOTE_ADDR", "")):
        return JsonResponse({"error": "Too many submissions"}, status=429)
    source = {"page": "/" if page is None else f"/{page.slug}", "form": form_id}
    for key in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"):
        value = request.POST.get(key, "").strip()[:200]
        if value:
            source[key] = value
    submission = queue_submission(
        tenant=tenant, page=page, form_id=form_id,
        email=email, name=name, phone=phone, message=message,
        consent=request.POST.get("consent", "").lower() in {"yes", "on", "true", "1"},
        source=source,
    )
    return JsonResponse({"event_id": str(submission.event_id), "queued": True}, status=202)
