from oauth2_provider.views import AuthorizationView

from api.auth import build_consent_contexts


class CmsAuthorizationView(AuthorizationView):
    """OAuth consent that surfaces tenant/role context for the approving user.

    Reuses django-oauth-toolkit's authorization_code + PKCE flow and CMS's
    existing ``LOGIN_URL`` via ``LoginRequiredMixin``.
    """

    template_name = "oauth2_provider/authorize.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["consent_contexts"] = build_consent_contexts(self.request.user)
        return context
