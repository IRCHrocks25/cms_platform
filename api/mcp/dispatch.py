"""JSON-RPC method table and audit seam for MCP tools/call."""

from __future__ import annotations

import logging
from typing import Any, Optional

from api.auth import ResolvedAuth
from api.models import McpAuditLog
from api.mcp import tools as tools_mod
from api.mcp.errors import INVALID_PARAMS, METHOD_NOT_FOUND, rpc_error


logger = logging.getLogger("api.mcp.audit")

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "katek-sites", "version": "1.0.0"}


def record_mcp_call(
    *,
    actor,
    tenant,
    tool: str,
    performed_via: str = McpAuditLog.VIA_MCP,
) -> None:
    """Persist one audit row per tools/call. Never raises into the read path.

    Bound: exactly one insert per call (no per-field / per-site fan-out).
    Arguments and field values are never stored. Insert failures are logged
    and swallowed so an audit outage cannot take down MCP reads.
    """
    try:
        McpAuditLog.objects.create(
            actor=actor if getattr(actor, "pk", None) else None,
            tenant=tenant,
            action=tool,
            performed_via=performed_via or McpAuditLog.VIA_MCP,
        )
    except Exception:
        logger.exception(
            "mcp_audit_write_failed actor_id=%s tenant=%s tool=%s via=%s",
            getattr(actor, "pk", None),
            getattr(tenant, "subdomain", None) if tenant is not None else None,
            tool,
            performed_via,
        )


def _initialize_result() -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
    }


def _valid_initialize_params(params: Any) -> bool:
    if not isinstance(params, dict):
        return False
    client = params.get("clientInfo") or {}
    return bool(
        params.get("protocolVersion")
        and isinstance(params.get("capabilities"), dict)
        and client.get("name")
        and client.get("version")
    )


def dispatch(
    auth: ResolvedAuth, method: str, params: Any
) -> tuple[Optional[Any], Optional[dict]]:
    """Return (result, error). Notifications are handled by the view."""
    if method == "initialize":
        if not _valid_initialize_params(params):
            return None, rpc_error(INVALID_PARAMS, "invalid initialize params")
        return _initialize_result(), None

    if method == "tools/list":
        return {"tools": tools_mod.TOOLS_LIST}, None

    if method == "tools/call":
        if not isinstance(params, dict):
            return None, rpc_error(INVALID_PARAMS, "invalid tools/call params")
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            return None, rpc_error(INVALID_PARAMS, "missing tool name")
        if not isinstance(arguments, dict):
            return None, rpc_error(INVALID_PARAMS, "arguments must be an object")

        # One audit row per call. Tenant stamped only when the principal may
        # reach it — denials and list_sites leave tenant NULL.
        site = arguments.get("site")
        tenant = None
        if isinstance(site, str) and site:
            from core.models import Tenant

            candidate = Tenant.objects.filter(subdomain=site).first()
            if candidate is not None and auth.for_tenant(candidate) is not None:
                tenant = candidate
        record_mcp_call(actor=auth.user, tenant=tenant, tool=name)

        result, err = tools_mod.call_tool(auth, name, arguments)
        if err is not None:
            return None, err
        return result, None

    if method == "notifications/initialized":
        # Notifications should not reach here with an id; view short-circuits.
        return {}, None

    return None, rpc_error(METHOD_NOT_FOUND, "method not found")
