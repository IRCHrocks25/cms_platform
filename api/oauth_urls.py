from django.urls import path
from oauth2_provider.urls import base_urlpatterns, metadata_urlpatterns
from oauth2_provider.views import IntrospectTokenView, RevokeTokenView, TokenView

from api.oauth_views import CmsAuthorizationView


app_name = "oauth2_provider"

# Deliberately omit django-oauth-toolkit's application-management and dynamic
# registration patterns. This product provisions exactly one static client via
# register_claude_oauth_client. Authorize is customized so consent shows
# tenant/role context; other base endpoints stay on the toolkit defaults.
_cms_base_urlpatterns = [
    path("authorize/", CmsAuthorizationView.as_view(), name="authorize"),
    path("token/", TokenView.as_view(), name="token"),
    path("revoke_token/", RevokeTokenView.as_view(), name="revoke_token"),
    path("introspect/", IntrospectTokenView.as_view(), name="introspect"),
]

# Keep device-flow routes from the toolkit base set (unused today, but stable).
_device_patterns = [
    p for p in base_urlpatterns if getattr(p, "name", None) not in {
        "authorize",
        "token",
        "revoke_token",
        "introspect",
    }
]

urlpatterns = metadata_urlpatterns + _cms_base_urlpatterns + _device_patterns
