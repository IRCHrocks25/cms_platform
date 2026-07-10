"""GHL (GoHighLevel) marketplace integration endpoints.

/embed/ auto-logs a CMS user in from a GHL Custom Menu Link. /connect/install/
starts the marketplace OAuth flow; /connect/callback/ exchanges the code and
persists encrypted credentials: an agency (Company) install enumerates its
sub-accounts into a GhlAgencyInstall, a single-location install into a
GhlInstall. The webhook is still a stub (signature verification is a
fast-follow)."""
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
from .ghl_crypto import TokenCryptoError, encrypt_token
from .models import GhlAgencyInstall, GhlInstall, Tenant

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
    """Absolute https URL of /connect/callback (NO trailing slash), used as the
    OAuth redirect_uri. GHL registers it without the trailing slash; a mismatch
    can bounce the user to the agency home. The no-slash route is served
    directly (see cms_platform/urls.py)."""
    return request.build_absolute_uri(reverse("ghl_oauth_callback")).rstrip("/")


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
    an access token, then branches on userType: Company installs enumerate
    sub-accounts and persist a GhlAgencyInstall; Location installs persist an
    encrypted GhlInstall. Both redirect to the Integrations dashboard.
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
    except ghl_oauth.TokenExchangeFailed:
        logger.exception("GHL callback: token exchange failed")
        return HttpResponse("Token exchange with GHL failed. Check server logs.", status=502)

    user_type = token_resp.get("userType", GhlInstall.USER_TYPE_LOCATION)
    company_id = token_resp.get("companyId", "")
    location_id = token_resp.get("locationId", "")
    access_token = token_resp.get("access_token", "")
    refresh_token = token_resp.get("refresh_token", "")
    expires_in = token_resp.get("expires_in")
    scope_str = token_resp.get("scope", "")

    expires_at = None
    if expires_in:
        try:
            expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            pass

    try:
        enc_access = encrypt_token(access_token)
        enc_refresh = encrypt_token(refresh_token)
    except TokenCryptoError:
        logger.exception("GHL callback: token encryption not configured")
        return HttpResponse("Server encryption is not configured. Check server logs.", status=503)

    if user_type == GhlInstall.USER_TYPE_COMPANY:
        if not company_id:
            return HttpResponse("Company token missing companyId.", status=502)
        app_id = (settings.GHL_CLIENT_ID or "").split("-")[0]
        try:
            locations = ghl_oauth.list_installed_locations(
                agency_access_token=access_token, company_id=company_id, app_id=app_id
            )
        except ghl_oauth.TokenExchangeFailed:
            logger.exception("GHL callback: installedLocations failed")
            return HttpResponse("Could not list sub-accounts from GHL. Check server logs.", status=502)
        if not locations:
            logger.warning("GHL agency install: company=%s has 0 installed locations", company_id)
        GhlAgencyInstall.objects.update_or_create(
            company_id=company_id,
            defaults={
                "company_name": token_resp.get("companyName", ""),
                "access_token": enc_access,
                "refresh_token": enc_refresh,
                "expires_at": expires_at,
                "scopes": scope_str.split() if scope_str else [],
                "available_locations": locations,
            },
        )
        logger.info("GHL agency install: company=%s locations=%d", company_id, len(locations))
        return redirect(f"{reverse('dashboard:integrations')}?connected=1")

    if not location_id:
        return HttpResponse("Token response missing locationId.", status=502)

    install, created = GhlInstall.objects.update_or_create(
        location_id=location_id,
        defaults={
            "company_id": company_id,
            "user_type": user_type,
            "access_token": enc_access,
            "refresh_token": enc_refresh,
            "expires_at": expires_at,
            "scopes": scope_str.split() if scope_str else [],
            "status": GhlInstall.STATUS_CONNECTED,
        },
    )
    tenant = Tenant.objects.filter(ghl_location_id=location_id).first()
    if tenant and install.tenant_id != tenant.id:
        install.tenant = tenant
        install.save(update_fields=["tenant", "updated_at"])
    logger.info("GHL install %s: location=%s", "created" if created else "refreshed", location_id)
    return redirect(f"{reverse('dashboard:integrations')}?connected=1")


@csrf_exempt
@require_POST
def webhook(request):
    """Receive GHL marketplace event notifications (install, uninstall, ...).

    Verifies the X-GHL-Signature (Ed25519) over the raw body when
    GHL_WEBHOOK_PUBLIC_KEY is configured, then dispatches the event.
    See core/ghl_webhook.py.
    """
    from . import ghl_webhook

    body = request.body or b""
    if ghl_webhook.signature_configured():
        if not ghl_webhook.verify_signature(
            body=body, signature_b64=request.headers.get("X-GHL-Signature", "")
        ):
            logger.warning("GHL webhook: signature verification failed")
            return HttpResponse("invalid signature", status=401)
    else:
        logger.warning(
            "GHL webhook: GHL_WEBHOOK_PUBLIC_KEY not set; accepting unverified"
        )
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("invalid JSON")
    ghl_webhook.handle_event(payload)
    logger.info(
        "GHL webhook: %s", payload.get("type") or payload.get("event") or "unknown"
    )
    return JsonResponse({"ok": True})


@require_http_methods(["GET"])
def privacy(request):
    return render(request, "legal/privacy.html")


@require_http_methods(["GET"])
def terms(request):
    return render(request, "legal/terms.html")
