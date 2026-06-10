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
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from . import ghl_oauth
from .models import GhlInstall, Tenant

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
    # Staff → agency-style editor; tenant members → their own subdomain
    # editor. The subdomain redirect requires the session cookie to span
    # *.sites.katek.app (set via SESSION_COOKIE_DOMAIN).
    if user.is_staff or user.is_superuser:
        return redirect(f"/dashboard/sites/{tenant.pk}/edit/")
    base = getattr(settings, "TENANT_BASE_DOMAIN", "") or ""
    if not base or base in {"localhost", "127.0.0.1"}:
        # Local dev: there's no per-subdomain TLS, so stay on the agency host.
        return redirect("/dashboard/")
    return redirect(f"https://{tenant.subdomain}.{base}/dashboard/")


def _build_redirect_uri(request) -> str:
    """Absolute https URL of /connect/callback/, used as the OAuth redirect_uri.
    Must match what's registered in the GHL marketplace app exactly."""
    return request.build_absolute_uri(reverse("ghl_oauth_callback"))


@require_http_methods(["GET"])
def oauth_install(request):
    """Kick off the OAuth install flow.

    GHL marketplace can link directly to its own ``chooselocation`` URL,
    but that path uses a state token GHL controls — short TTL, sometimes
    stuck in marketplace cookies after an uninstall, producing
    "Invalid state: Signature has expired" on reinstall. Going through
    /connect/install/ lets us sign our own state with a 30-minute TTL we
    control end-to-end.
    """
    if not settings.GHL_CLIENT_ID:
        return HttpResponse("GHL_CLIENT_ID not configured.", status=503)
    state = ghl_oauth.sign_state({"source": "install"})
    url = ghl_oauth.build_install_url(
        state=state,
        redirect_uri=_build_redirect_uri(request),
    )
    return redirect(url)


@require_http_methods(["GET"])
def oauth_callback(request):
    """OAuth callback. GHL redirects here after the user authorizes.

    Receives ?code= and ?state=. Verifies our state, exchanges the code for
    an access token, normalizes Company tokens to Location tokens, and
    persists a GhlInstall row keyed by location_id.
    """
    error = request.GET.get("error")
    if error:
        return HttpResponse(f"GHL authorization failed: {error}", status=400)

    code = request.GET.get("code", "").strip()
    state = request.GET.get("state", "").strip()
    if not code:
        return HttpResponseBadRequest("missing code")

    # State is only present when the install kicked off from our own
    # /connect/install/ link. Marketplace-initiated installs come straight
    # from GHL with no state — accept those without verification, since the
    # user clicking "Install" inside GHL is the authorization signal.
    if state:
        try:
            ghl_oauth.verify_state(state)
        except ghl_oauth.StateInvalid as exc:
            logger.warning("GHL callback: state rejected (%s)", exc)
            return HttpResponse(
                "Authorization link expired. Please re-start the install from GHL.",
                status=400,
            )
    else:
        logger.info("GHL callback: marketplace-initiated install (no state)")

    try:
        token_resp = ghl_oauth.exchange_code(
            code=code, redirect_uri=_build_redirect_uri(request),
        )
    except ghl_oauth.TokenExchangeFailed as exc:
        logger.exception("GHL callback: token exchange failed")
        return HttpResponse(f"Token exchange failed: {exc}", status=502)

    user_type = token_resp.get("userType", GhlInstall.USER_TYPE_LOCATION)
    company_id = token_resp.get("companyId", "")
    location_id = token_resp.get("locationId", "")
    access_token = token_resp.get("access_token", "")
    refresh_token = token_resp.get("refresh_token", "")
    expires_in = token_resp.get("expires_in")
    scope_str = token_resp.get("scope", "")

    # Agency owner installs come back as Company tokens with no locationId.
    # Mint a Location-token for at least one installed sub-account so the
    # GhlInstall row has a concrete location_id to key on.
    if user_type == GhlInstall.USER_TYPE_COMPANY:
        if not company_id:
            return HttpResponse("Company token missing companyId.", status=502)
        # TODO: enumerate installed locations and mint one Location token per.
        # For now record the Company-level install so the user sees a success.
        logger.info("GHL install: Company-level (companyId=%s)", company_id)
        location_id = location_id or f"company:{company_id}"

    if not location_id:
        return HttpResponse("Token response missing locationId.", status=502)

    expires_at = None
    if expires_in:
        try:
            expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            pass

    install, created = GhlInstall.objects.update_or_create(
        location_id=location_id,
        defaults={
            "company_id": company_id,
            "user_type": user_type,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "scopes": scope_str.split() if scope_str else [],
        },
    )
    logger.info(
        "GHL install %s: location=%s userType=%s",
        "created" if created else "refreshed", location_id, user_type,
    )

    # If a tenant has already been mapped to this location_id, link it.
    tenant = Tenant.objects.filter(ghl_location_id=location_id).first()
    if tenant and install.tenant_id != tenant.id:
        install.tenant = tenant
        install.save(update_fields=["tenant", "updated_at"])

    return render(
        request,
        "ghl/install_success.html",
        {"location_id": location_id, "tenant": tenant},
    )


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
