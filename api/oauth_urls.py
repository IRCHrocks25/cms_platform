from django.urls import path
from oauth2_provider.urls import base_urlpatterns, metadata_urlpatterns
from oauth2_provider.views import IntrospectTokenView, RevokeTokenView, TokenView

from api.oauth_dcr import (
    CmsDynamicClientRegistrationManagementView,
    CmsDynamicClientRegistrationView,
)
from api.oauth_views import CmsAuthorizationView


app_name = "oauth2_provider"

# Authorize is customized so consent shows tenant/role context; other base
# endpoints stay on the toolkit defaults. DCR uses DOT 3.4.0's RFC 7591
# implementation (api.oauth_dcr) mounted at /oauth/register to match IBC.
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

# No trailing slash — matches IBC's /oauth/register and avoids APPEND_SLASH
# turning a POST into a body-dropping redirect.
_dcr_urlpatterns = [
    path(
        "oauth/register",
        CmsDynamicClientRegistrationView.as_view(),
        name="dcr-register",
    ),
    path(
        "oauth/register/<str:client_id>",
        CmsDynamicClientRegistrationManagementView.as_view(),
        name="dcr-register-management",
    ),
]

urlpatterns = (
    metadata_urlpatterns
    + _cms_base_urlpatterns
    + _device_patterns
    + _dcr_urlpatterns
)
