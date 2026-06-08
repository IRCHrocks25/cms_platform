"""GHL (GoHighLevel) marketplace integration endpoints.

Phase 1 (current): /embed/ auto-logs in a CMS user when the GHL Custom Menu
Link delivers ?location_id=&email= via template substitution. We trust the
URL params — Phase 2 will replace this with verified signed context once
the OAuth marketplace install flow is built.

The other endpoints (callback, webhook, privacy, terms) are stubs so the
GHL marketplace app form can validate them; install/uninstall handling and
real OAuth come next."""
import json
import logging

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .models import Tenant

logger = logging.getLogger(__name__)
User = get_user_model()


@require_http_methods(["GET"])
def embed_view(request):
    """Entry point loaded inside the GHL iframe.

    GHL Custom Menu Link URL template:
      https://sites.katek.app/embed/?location_id={{location.id}}&email={{user.email}}

    Trust model (Phase 1): URL params are unsigned. Acceptable for an
    agency-private menu link, NOT for a public marketplace app. The
    GHL_AUTO_LOGIN env var is the kill switch — disabled by default.
    """
    if not settings.GHL_AUTO_LOGIN:
        raise Http404

    location_id = (request.GET.get("location_id") or "").strip()
    email = (request.GET.get("email") or "").strip().lower()

    if not location_id:
        return redirect("/login/?error=missing_ghl_context")

    tenant = get_object_or_404(Tenant, ghl_location_id=location_id)

    # Pick a user: prefer the GHL-provided email if it matches an active CMS
    # user who can edit this tenant, otherwise fall back to the tenant owner.
    # This makes the URL-param flow forgiving for sub-accounts where the GHL
    # email doesn't yet map to a CMS user.
    user = None
    if email:
        try:
            candidate = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist:
            candidate = None
        except User.MultipleObjectsReturned:
            logger.warning("GHL embed: multiple active users with email %r", email)
            candidate = None
        if candidate is not None and tenant.user_can_edit(candidate):
            user = candidate

    if user is None:
        user = tenant.owner
        if not user.is_active:
            return HttpResponse("Tenant owner inactive", status=403)
        logger.info("GHL embed: email %r did not match — falling back to tenant.owner", email)

    user.backend = "django.contrib.auth.backends.ModelBackend"
    login(request, user)
    logger.info(
        "GHL embed: logged in %s for tenant %s (location %s)",
        user.email, tenant.subdomain, location_id,
    )
    return redirect(f"/dashboard/sites/{tenant.pk}/edit/")


@require_http_methods(["GET"])
def oauth_callback(request):
    """OAuth callback for the GHL marketplace install flow.

    Currently a stub: returns 200 so the marketplace app form validates.
    Real implementation: exchange ?code= for an access_token via GHL's
    /oauth/token endpoint using GHL_CLIENT_ID + GHL_CLIENT_SECRET, persist
    the token bound to a Tenant, redirect to a "you're connected" page.
    """
    code = request.GET.get("code", "")
    logger.info("GHL oauth callback received code=%r (stub)", code[:8] + "..." if code else "")
    return HttpResponse("GHL OAuth callback received. Integration coming soon.", status=200)


@csrf_exempt
@require_POST
def webhook(request):
    """Receives event notifications from GHL (install, uninstall, etc.).

    Stub: accepts and logs; no signature verification yet. Real impl will
    verify a shared-secret header before acting.
    """
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("invalid JSON")
    logger.info("GHL webhook: %s", payload.get("type", "unknown"))
    return JsonResponse({"ok": True})


@require_http_methods(["GET"])
def privacy(request):
    return render(request, "legal/privacy.html")


@require_http_methods(["GET"])
def terms(request):
    return render(request, "legal/terms.html")
