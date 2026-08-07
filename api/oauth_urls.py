from oauth2_provider.urls import base_urlpatterns, metadata_urlpatterns


app_name = "oauth2_provider"

# Deliberately omit django-oauth-toolkit's application-management and dynamic
# registration patterns. This product provisions exactly one static client via
# register_claude_oauth_client.
urlpatterns = metadata_urlpatterns + base_urlpatterns
