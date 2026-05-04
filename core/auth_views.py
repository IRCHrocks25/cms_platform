from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.views import LoginView
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


class TenantAwareLoginView(LoginView):
    """Login view that routes the user based on host + tenant membership.

    - On a tenant host: log in if member/staff, otherwise refuse.
    - On the agency host: log in if staff/superuser, otherwise refuse.
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

        messages.error(request, "This account has no sites here.")
        return HttpResponseRedirect(reverse("login"))

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
