"""MCP tool definitions and handlers."""

from __future__ import annotations

from typing import Any, Callable, Optional

from django.conf import settings

from core.models import Template, Tenant
from core.services.accounts import create_tenant_account

from api.auth import ResolvedAuth, TenantScope
from api.mcp import content as content_mod
from api.mcp.errors import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    page_access_denied,
    site_access_denied,
    tool_error,
    tool_success,
)


ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

WRITE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}

# Privileged-tool denial — identical for every non-superuser principal so the
# response does not reveal whether inputs were valid or what exists.
_CREATE_DENIED = tool_error("Not permitted.")


def _site_row(tenant: Tenant, role: str) -> dict[str, Any]:
    return {
        "subdomain": tenant.subdomain,
        "name": tenant.name,
        "role": role,
        "published": tenant.is_published,
    }


def list_sites(auth: ResolvedAuth) -> dict[str, Any]:
    if auth.platform_role:
        sites = [
            _site_row(t, auth.platform_role)
            for t in Tenant.objects.order_by("name")
        ]
    else:
        sites = [
            _site_row(scope.tenant, scope.role) for scope in auth.tenant_scopes
        ]
    return {"sites": sites}


def _require_scope(auth: ResolvedAuth, site: str) -> Optional[TenantScope]:
    tenant = Tenant.objects.filter(subdomain=site).first()
    if tenant is None:
        return None
    return auth.for_tenant(tenant)


def list_pages(auth: ResolvedAuth, *, site: str) -> dict[str, Any]:
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    tenant = scope.tenant
    pages = [
        {
            "page": None,
            "title": tenant.name,
            "published": tenant.is_published,
            "is_home": True,
        }
    ]
    for page in tenant.pages.order_by("nav_order", "title"):
        pages.append(
            {
                "page": page.slug,
                "title": page.title,
                "published": page.is_published,
                "is_home": False,
            }
        )
    return tool_success({"pages": pages})


def get_page(
    auth: ResolvedAuth, *, site: str, page: Optional[str] = None
) -> dict[str, Any]:
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        _editable, schema, stored = content_mod.resolve_editable(
            scope.tenant, page
        )
    except LookupError:
        return page_access_denied(site, page or "")
    fields = content_mod.read_fields(schema, stored)
    return tool_success(
        {"fields": fields, "etag": content_mod.content_etag(stored)}
    )


def get_content(
    auth: ResolvedAuth,
    *,
    site: str,
    field: str,
    page: Optional[str] = None,
) -> dict[str, Any]:
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        _editable, schema, stored = content_mod.resolve_editable(
            scope.tenant, page
        )
    except LookupError:
        return page_access_denied(site, page or "")
    try:
        value, is_default = content_mod.read_field(schema, stored, field)
    except KeyError:
        return tool_error(f"Unknown field '{field}'.")
    return tool_success(
        {
            "value": value,
            "is_default": is_default,
            "etag": content_mod.content_etag(stored),
        }
    )


def _public_site_url(subdomain: str) -> str:
    """Build the new site's public URL from TENANT_BASE_DOMAIN (never hardcode)."""
    base = (settings.TENANT_BASE_DOMAIN or "").strip(".").lower()
    if not base or base == "localhost" or base.endswith(".local"):
        return f"http://{subdomain}.{base or 'localhost'}/"
    return f"https://{subdomain}.{base}/"


def create_client_account(
    auth: ResolvedAuth,
    *,
    name: str,
    subdomain: str,
    username: str,
    email: str,
    template_id: int,
    custom_domain: str = "",
) -> dict[str, Any]:
    """CMS-11 + CMS-8: mint tenant + owner login; password returned once only."""
    if not getattr(auth.user, "is_superuser", False):
        return _CREATE_DENIED

    subdomain = (subdomain or "").strip().lower()
    username = (username or "").strip()
    email = (email or "").strip()
    name = (name or "").strip()
    custom_domain = (custom_domain or "").strip().lower()

    if not name or not subdomain or not username:
        return tool_error("name, subdomain, and username are required.")

    reserved = getattr(settings, "TENANT_RESERVED_SUBDOMAINS", set()) or set()
    if subdomain in reserved:
        return tool_error(f"Subdomain '{subdomain}' is reserved.")
    if Tenant.objects.filter(subdomain=subdomain).exists():
        return tool_error(f"Subdomain '{subdomain}' is already taken.")

    from django.contrib.auth import get_user_model

    User = get_user_model()
    if User.objects.filter(username__iexact=username).exists():
        return tool_error(f"Username '{username}' is already taken.")

    template = Template.objects.filter(pk=template_id).first()
    if template is None:
        return tool_error(f"Unknown template_id {template_id}.")

    tenant, user, password = create_tenant_account(
        name=name,
        subdomain=subdomain,
        custom_domain=custom_domain,
        template=template,
        username=username,
        email=email,
        is_published=False,
    )
    return tool_success(
        {
            "subdomain": tenant.subdomain,
            "name": tenant.name,
            "username": user.username,
            "password": password,
            "site_url": _public_site_url(tenant.subdomain),
            "published": tenant.is_published,
            "template_id": template.pk,
        }
    )


# list_sites returns a plain dict; wrap at call site for MCP envelope.
HANDLERS: dict[str, Callable[..., Any]] = {
    "list_sites": list_sites,
    "list_pages": list_pages,
    "get_page": get_page,
    "get_content": get_content,
    "create_client_account": create_client_account,
}


TOOLS_LIST: list[dict[str, Any]] = [
    {
        "name": "list_sites",
        "description": "List sites the caller can access.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "required": ["sites"],
            "additionalProperties": False,
            "properties": {
                "sites": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["subdomain", "name", "role", "published"],
                        "additionalProperties": False,
                        "properties": {
                            "subdomain": {"type": "string"},
                            "name": {"type": "string"},
                            "role": {"type": "string"},
                            "published": {"type": "boolean"},
                        },
                    },
                }
            },
        },
        "annotations": ANNOTATIONS,
    },
    {
        "name": "list_pages",
        "description": "List pages for a site, including the home page.",
        "inputSchema": {
            "type": "object",
            "required": ["site"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string", "description": "Tenant subdomain"},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["pages"],
            "additionalProperties": False,
            "properties": {
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["page", "title", "published", "is_home"],
                        "additionalProperties": False,
                        "properties": {
                            "page": {"type": ["string", "null"]},
                            "title": {"type": "string"},
                            "published": {"type": "boolean"},
                            "is_home": {"type": "boolean"},
                        },
                    },
                }
            },
        },
        "annotations": ANNOTATIONS,
    },
    {
        "name": "get_page",
        "description": "Read all editable fields for a page (merged with defaults).",
        "inputSchema": {
            "type": "object",
            "required": ["site"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "page": {
                    "type": ["string", "null"],
                    "description": "Page slug; omit/null for home",
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["fields", "etag"],
            "additionalProperties": False,
            "properties": {
                "fields": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["value", "is_default"],
                        "properties": {
                            "value": {},
                            "is_default": {"type": "boolean"},
                        },
                    },
                },
                "etag": {"type": "string"},
            },
        },
        "annotations": ANNOTATIONS,
    },
    {
        "name": "get_content",
        "description": "Read one field by dotted id.",
        "inputSchema": {
            "type": "object",
            "required": ["site", "field"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "field": {"type": "string"},
                "page": {"type": ["string", "null"]},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["value", "is_default", "etag"],
            "additionalProperties": False,
            "properties": {
                "value": {},
                "is_default": {"type": "boolean"},
                "etag": {"type": "string"},
            },
        },
        "annotations": ANNOTATIONS,
    },
    {
        "name": "create_client_account",
        "description": (
            "Create a new client site and owner login. Superuser only. "
            "Requires an explicit template_id — there is no default template. "
            "Sites are created unpublished. The generated password is returned "
            "once in the tool result and is never stored in plaintext."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["name", "subdomain", "username", "email", "template_id"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "description": "Site display name"},
                "subdomain": {"type": "string", "description": "Tenant subdomain"},
                "username": {"type": "string", "description": "Owner login username"},
                "email": {"type": "string", "description": "Owner email"},
                "template_id": {
                    "type": "integer",
                    "description": "Template primary key (required; no default)",
                },
                "custom_domain": {
                    "type": "string",
                    "description": "Optional custom domain hostname",
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "required": [
                "subdomain",
                "name",
                "username",
                "password",
                "site_url",
                "published",
                "template_id",
            ],
            "additionalProperties": False,
            "properties": {
                "subdomain": {"type": "string"},
                "name": {"type": "string"},
                "username": {"type": "string"},
                "password": {"type": "string"},
                "site_url": {"type": "string"},
                "published": {"type": "boolean"},
                "template_id": {"type": "integer"},
            },
        },
        "annotations": WRITE_ANNOTATIONS,
    },
]


def _validate_arguments(tool_name: str, arguments: dict) -> Optional[str]:
    tool = next((t for t in TOOLS_LIST if t["name"] == tool_name), None)
    if tool is None:
        return None
    schema = tool["inputSchema"]
    required = schema.get("required") or []
    for key in required:
        if key not in arguments or arguments[key] in (None, ""):
            # page may be null for get_page; only enforce non-null for required site/field
            if key == "page":
                continue
            return f"Missing required argument '{key}'"
    if schema.get("additionalProperties") is False:
        allowed = set((schema.get("properties") or {}).keys())
        extra = set(arguments) - allowed
        if extra:
            return f"Unexpected arguments: {sorted(extra)}"
    for key, prop in (schema.get("properties") or {}).items():
        if key not in arguments:
            continue
        value = arguments[key]
        expected = prop.get("type")
        if expected is None:
            continue
        if isinstance(expected, list):
            if value is None and "null" in expected:
                continue
            if "string" in expected and isinstance(value, str):
                continue
            if value is None and "null" in expected:
                continue
            if not any(
                (t == "string" and isinstance(value, str))
                or (t == "null" and value is None)
                or (t == "boolean" and isinstance(value, bool))
                for t in expected
            ):
                return f"Invalid type for '{key}'"
        elif expected == "string" and not isinstance(value, str):
            return f"Invalid type for '{key}'"
        elif expected == "boolean" and not isinstance(value, bool):
            return f"Invalid type for '{key}'"
        elif expected == "integer" and not isinstance(value, int):
            return f"Invalid type for '{key}'"
    return None


def call_tool(
    auth: ResolvedAuth, name: str, arguments: Optional[dict]
) -> tuple[Optional[dict], Optional[dict]]:
    """Return (result, rpc_error). Exactly one is set."""
    if name not in HANDLERS:
        return None, {"code": METHOD_NOT_FOUND, "message": f"Unknown tool '{name}'"}
    args = dict(arguments or {})
    err = _validate_arguments(name, args)
    if err:
        return None, {"code": INVALID_PARAMS, "message": err}

    if name == "list_sites":
        return tool_success(list_sites(auth)), None

    # Privileged tools: deny non-superusers before any lookup that could leak.
    if name == "create_client_account" and not getattr(
        auth.user, "is_superuser", False
    ):
        return _CREATE_DENIED, None

    handler = HANDLERS[name]
    # Drop unknown optional nulls cleanly
    if "page" in args and args["page"] is None:
        args.pop("page")
    if "custom_domain" in args and args["custom_domain"] in (None, ""):
        args.pop("custom_domain")
    return handler(auth, **args), None
