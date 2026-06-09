from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.views import (
    LoginView,
    PasswordResetConfirmView,
    PasswordResetView,
)
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme

from core.urls_helpers import tenant_editor_url, tenant_login_url


class TenantAwareLoginView(LoginView):
    """Login view that routes the user based on host + tenant membership.

    - On a tenant host: log in if member/staff, otherwise refuse.
    - On the agency host: staff/superuser → agency dashboard; a non-staff client
      is routed to their own site's editor instead of being refused.
    """

    template_name = "auth/login.html"

    def form_valid(self, form):
        user = form.get_user()
        request = self.request
        tenant = getattr(request, "tenant", None)

        if tenant is not None:
            if not tenant.user_can_edit(user):
                messages.error(request, "This account has no access to this site.")
                return HttpResponseRedirect(reverse("login"))
            auth_login(request, user)
            return HttpResponseRedirect(self._safe_next() or reverse("dashboard:root"))

        if user.is_staff or user.is_superuser:
            auth_login(request, user)
            return HttpResponseRedirect(self._safe_next() or reverse("dashboard:root"))

        # Non-staff client logging in on the main/agency host: send them to
        # their own site rather than refusing.
        home_tenant = self._pick_home_tenant(user)
        if home_tenant is not None:
            if getattr(settings, "SESSION_COOKIE_DOMAIN", None):
                # Production: the session cookie spans the parent domain
                # (COOKIE_PARENT_DOMAIN), so logging in here carries straight
                # over to the subdomain editor — one login, no second prompt.
                auth_login(request, user)
                return HttpResponseRedirect(tenant_editor_url(request, home_tenant))
            # Single-host / local dev: the cookie can't span hosts, so bounce
            # them to their own site's login to establish the session there.
            return HttpResponseRedirect(tenant_login_url(request, home_tenant))

        messages.error(request, "This account has no sites here.")
        return HttpResponseRedirect(reverse("login"))

    def _pick_home_tenant(self, user):
        """Which site to drop a client into after an agency-host login.

        Prefers a site they own; otherwise their earliest membership. Returns
        None when the user belongs to no site.
        """
        from core.models import TenantMembership

        memberships = list(
            TenantMembership.objects.select_related("tenant")
            .filter(user=user)
            .order_by("created_at")
        )
        if not memberships:
            return None
        for m in memberships:
            if m.tenant.owner_id == user.id:
                return m.tenant
        return memberships[0].tenant

    def _safe_next(self):
        request = self.request
        next_url = request.POST.get("next") or request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return next_url
        return None


# --------------------------------------------------------------------------- #
# Self-service password reset (tenant-aware, Resend email, rate-limited).      #
#                                                                              #
# Uses Django's built-in reset machinery (default_token_generator — signed,   #
# expiring per PASSWORD_RESET_TIMEOUT). The email link points back to the      #
# SAME host the request came in on: with django.contrib.sites NOT installed,  #
# Django's get_current_site() returns a RequestSite(request), so the link's   #
# domain is request.get_host() — i.e. the client's own subdomain.             #
# --------------------------------------------------------------------------- #

_RESET_RATE_LIMIT = 5          # max reset requests
_RESET_RATE_WINDOW = 60 * 60   # per hour, per IP and per email


def _rate_limited(*keys) -> bool:
    """True if any of ``keys`` has exceeded the window's allowance. Increments
    each key's counter. Best-effort (uses the configured Django cache)."""
    limited = False
    for key in keys:
        try:
            count = cache.get(key, 0)
            if count >= _RESET_RATE_LIMIT:
                limited = True
            cache.set(key, count + 1, _RESET_RATE_WINDOW)
        except Exception:
            # Never let a cache hiccup block a legitimate reset.
            pass
    return limited


class TenantPasswordResetForm(PasswordResetForm):
    """Restrict the reset to accounts that can actually edit the current tenant,
    so a reset requested on one subdomain can't target unrelated accounts. On
    the agency host (no tenant) it behaves like the default form."""

    tenant = None

    def get_users(self, email):
        users = super().get_users(email)
        if self.tenant is None:
            return users
        return (u for u in users if self.tenant.user_can_edit(u))


class TenantPasswordResetView(PasswordResetView):
    template_name = "auth/password_reset_form.html"
    email_template_name = "auth/password_reset_email.txt"
    subject_template_name = "auth/password_reset_subject.txt"
    form_class = TenantPasswordResetForm
    success_url = reverse_lazy("password_reset_done")

    def post(self, request, *args, **kwargs):
        ip = request.META.get("REMOTE_ADDR", "")
        email = (request.POST.get("email") or "").strip().lower()
        # Non-revealing throttle: when over the limit, show the same "sent"
        # confirmation but skip processing entirely (no email, no enumeration).
        if _rate_limited(f"pwreset:ip:{ip}", f"pwreset:email:{email}"):
            return HttpResponseRedirect(self.get_success_url())
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        form.tenant = getattr(self.request, "tenant", None)
        return super().form_valid(form)


class TenantPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "auth/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")
