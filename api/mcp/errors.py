"""JSON-RPC error helpers for the MCP transport."""

from __future__ import annotations

import json
from typing import Any


PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def rpc_error(code: int, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


def tool_error(text: str) -> dict[str, Any]:
    """Tool-level failure (HTTP 200, isError true)."""
    return {
        "content": [{"type": "text", "text": text}],
        "isError": True,
    }


def tool_success(structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(structured, ensure_ascii=False),
            }
        ],
        "structuredContent": structured,
        "isError": False,
    }


def site_access_denied(site: str) -> dict[str, Any]:
    return tool_error(
        f"No accessible site '{site}'. Call list_sites to see available sites."
    )


def page_access_denied(site: str, page: str) -> dict[str, Any]:
    return tool_error(
        f"No accessible page '{page}' on site '{site}'. "
        "Call list_pages to see available pages."
    )
