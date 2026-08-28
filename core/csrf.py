from django.http import JsonResponse
from django.views.csrf import csrf_failure as django_csrf_failure


def csrf_failure(request, reason=""):
    """Return JSON for fetch/XHR so annotation and other AJAX POSTs can
    show a refresh hint instead of a bare HTTP 403 with an HTML body."""
    accept = request.headers.get("Accept", "")
    content_type = request.content_type or ""
    wants_json = (
        "application/json" in accept
        or content_type == "application/json"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )
    if wants_json:
        return JsonResponse(
            {
                "error": (
                    "This page's security token expired. "
                    "Refresh and run AI annotation again."
                )
            },
            status=403,
        )
    return django_csrf_failure(request, reason=reason)
