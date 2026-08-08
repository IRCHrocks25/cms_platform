"""HTTP transport for the Streamable HTTP MCP endpoint."""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlparse

from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from api.auth import resolve_access_token
from api.mcp.dispatch import PROTOCOL_VERSION, dispatch
from api.mcp.errors import INVALID_REQUEST, PARSE_ERROR, rpc_error


MAX_BODY_BYTES = 16 * 1024 * 1024


def _allowed_origins() -> set[str]:
    raw = getattr(settings, "MCP_ALLOWED_ORIGINS", "") or ""
    out: set[str] = set()
    for part in raw.split(","):
        canonical = _canonical_origin(part)
        if canonical:
            out.add(canonical)
    return out


def _canonical_origin(raw: str) -> str:
    parsed = urlparse((raw or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    if parsed.path not in ("", "/"):
        return ""
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _wants_sse(request) -> bool:
    accept = request.headers.get("Accept", "")
    return "text/event-stream" in accept and "application/json" not in accept


def _www_authenticate(request) -> str:
    host = request.get_host()
    scheme = "https" if request.is_secure() else "http"
    metadata = (
        f'{scheme}://{host}/.well-known/oauth-protected-resource/mcp'
    )
    return f'Bearer realm="katek-sites", resource_metadata="{metadata}"'


def _unauthorized(request) -> HttpResponse:
    response = HttpResponse(status=401)
    response["WWW-Authenticate"] = _www_authenticate(request)
    return response


def _write_rpc(
    request, status: int, payload: dict[str, Any]
) -> HttpResponse:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if _wants_sse(request):
        response = HttpResponse(
            b"event: message\ndata: " + body + b"\n\n",
            status=status,
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        return response
    return HttpResponse(body, status=status, content_type="application/json")


def _bearer_token(request) -> Optional[str]:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


@method_decorator(csrf_exempt, name="dispatch")
class McpView(View):
    """Agency-host MCP endpoint at ``/mcp`` (no trailing slash)."""

    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def dispatch(self, request, *args, **kwargs):
        # Host binding: agency only.
        if getattr(request, "tenant", None) is not None:
            return HttpResponse(status=404)

        origin = request.headers.get("Origin")
        if origin:
            if _canonical_origin(origin) not in _allowed_origins():
                return HttpResponse("untrusted Origin", status=403)

        if request.method not in ("GET", "POST"):
            response = HttpResponse(status=405)
            response["Allow"] = "GET, POST"
            return response

        token = _bearer_token(request)
        auth = resolve_access_token(token) if token else None
        if auth is None:
            return _unauthorized(request)

        request.mcp_auth = auth
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        if request.headers.get("MCP-Protocol-Version") != PROTOCOL_VERSION:
            return HttpResponse(
                "unsupported MCP-Protocol-Version", status=400
            )

        def event_stream():
            yield b": connected\n\n"

        response = StreamingHttpResponse(
            event_stream(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["Connection"] = "keep-alive"
        return response

    def post(self, request, *args, **kwargs):
        raw = request.body
        if len(raw) > MAX_BODY_BYTES:
            return _write_rpc(
                request,
                413,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": rpc_error(INVALID_REQUEST, "request body too large"),
                },
            )

        try:
            payload = json.loads(raw.decode("utf-8") if raw else "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _write_rpc(
                request,
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": rpc_error(PARSE_ERROR, "parse error"),
                },
            )

        if not isinstance(payload, dict):
            return _write_rpc(
                request,
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": rpc_error(INVALID_REQUEST, "invalid request"),
                },
            )

        # Reject trailing junk by requiring a single top-level value already
        # parsed; additionally detect concatenated JSON via a second decode.
        decoder = json.JSONDecoder()
        try:
            _obj, idx = decoder.raw_decode(raw.decode("utf-8"))
            if raw.decode("utf-8")[idx:].strip():
                return _write_rpc(
                    request,
                    400,
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "error": rpc_error(INVALID_REQUEST, "invalid request"),
                    },
                )
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _write_rpc(
                request,
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": rpc_error(PARSE_ERROR, "parse error"),
                },
            )

        method = payload.get("method")
        if payload.get("jsonrpc") != "2.0" or not isinstance(method, str) or not method:
            return _write_rpc(
                request,
                400,
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": rpc_error(INVALID_REQUEST, "invalid request"),
                },
            )

        protocol_header = request.headers.get("MCP-Protocol-Version", "")
        if protocol_header != PROTOCOL_VERSION and (
            method != "initialize" or protocol_header != ""
        ):
            return _write_rpc(
                request,
                400,
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "error": rpc_error(
                        INVALID_REQUEST, "unsupported MCP-Protocol-Version"
                    ),
                },
            )

        # JSON-RPC notification (no id) → 202 empty body.
        if "id" not in payload:
            return HttpResponse(status=202)

        result, error = dispatch(
            request.mcp_auth, method, payload.get("params")
        )
        response_body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
        }
        if error is not None:
            response_body["error"] = error
        else:
            response_body["result"] = result
        return _write_rpc(request, 200, response_body)
