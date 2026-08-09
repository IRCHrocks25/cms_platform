"""MCP tool definitions and handlers."""

from __future__ import annotations

from typing import Any, Callable, Optional

from django.conf import settings

from django.db import transaction

from core.models import Page, RESERVED_PAGE_SLUGS, Template, Tenant
from core.services import content_versions as cv
from core.services.accounts import create_tenant_account
from core.services.templates import FieldLossError, save_template_version
from core.urls_helpers import tenant_canonical_public_url

from api.auth import ResolvedAuth, TenantScope
from api.mcp import content as content_mod
from api.mcp.errors import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    page_access_denied,
    site_access_denied,
    tool_conflict,
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
    if template.tenant_id is not None:
        return tool_error(
            f"template_id {template_id} is client-owned. "
            "Pass a library template id (unowned), or duplicate that "
            "template into the agency library first."
        )

    from core.services.templates import CrossTenantTemplateError

    try:
        tenant, user, password = create_tenant_account(
            name=name,
            subdomain=subdomain,
            custom_domain=custom_domain,
            template=template,
            username=username,
            email=email,
            is_published=False,
        )
    except CrossTenantTemplateError as exc:
        return tool_error(str(exc))

    return tool_success(
        {
            "subdomain": tenant.subdomain,
            "name": tenant.name,
            "username": user.username,
            "password": password,
            "site_url": _public_site_url(tenant.subdomain),
            "published": tenant.is_published,
            "template_id": tenant.template_id,
        }
    )


def patch_content(
    auth: ResolvedAuth,
    *,
    site: str,
    field: str,
    value: Any,
    if_match: str,
    page: Optional[str] = None,
) -> dict[str, Any]:
    """Write one home-page field by dotted id. Inner pages are refused."""
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)

    # Inner Page content has no ContentVersion undo — refuse rather than
    # silently write unversioned data (CMS-7 decision).
    if page is not None and page != "":
        return tool_error(
            "patch_content only supports the home page until page versioning "
            "exists. Omit 'page' (or pass null) to edit the site home."
        )

    try:
        editable, schema, stored = content_mod.resolve_editable(
            scope.tenant, None
        )
    except LookupError:
        return page_access_denied(site, page or "")

    tpl = editable.template
    if not tpl.is_client_editable and not (
        getattr(auth.user, "is_superuser", False)
        or getattr(auth.user, "is_staff", False)
    ):
        return tool_error(
            "This site isn't set up for editing yet — contact your agency."
        )

    current_etag = content_mod.content_etag(stored)
    if if_match != current_etag:
        return tool_conflict(
            "Conflict (409): content has changed since if_match. "
            "Re-read with get_content and retry."
        )

    try:
        content_mod.read_field(schema, stored, field)
    except KeyError:
        return tool_error(f"Unknown field '{field}'.")

    try:
        new_content = content_mod.write_field(stored, field, value)
    except ValueError as exc:
        return tool_error(str(exc))

    cv.save_tenant_content(
        editable,
        new_content,
        user=auth.user,
        source=cv.SOURCE_MCP,
    )
    editable.refresh_from_db()
    new_etag = content_mod.content_etag(editable.content or {})
    url = tenant_canonical_public_url(editable)
    return tool_success({"etag": new_etag, "url": url})


# Indistinguishable denial for missing / foreign template_id (no enumeration).
_TEMPLATE_DENIED = tool_error(
    "No accessible template for this site. "
    "Omit template_id to create or update the page's own template."
)


def _ensure_tenant_owned_template(
    tenant: Tenant,
    template: Template,
    *,
    user,
) -> Template:
    """Return a template owned by ``tenant``. Refuse foreign owners; fork library."""
    if template.tenant_id == tenant.pk:
        return template
    if template.tenant_id is None:
        return template.clone_for(tenant, user=user)
    raise LookupError("foreign template")


def _push_html_onto_template(
    template: Template,
    html: str,
    *,
    user,
    allow_field_loss: bool,
    if_match: Optional[str],
    label: str,
) -> tuple[Optional[dict], Optional[Any]]:
    """Apply if_match + save_template_version. Returns (error_result, SaveTemplateResult)."""
    current_etag = content_mod.html_etag(template.html_source)
    if if_match is not None and if_match != current_etag:
        return (
            tool_conflict(
                "Conflict (409): template has changed since if_match. "
                "Re-read and retry with the current etag."
            ),
            None,
        )
    try:
        result = save_template_version(
            template,
            html,
            user=user,
            allow_field_loss=bool(allow_field_loss),
            label=label,
        )
    except FieldLossError as exc:
        return tool_error(str(exc)), None
    return None, result


def push_page(
    auth: ResolvedAuth,
    *,
    site: str,
    html: str,
    page: Optional[str] = None,
    title: str = "",
    allow_field_loss: bool = False,
    if_match: Optional[str] = None,
    template_id: Optional[int] = None,
) -> dict[str, Any]:
    """CMS-10: create or whole-HTML re-push a page (or the site home).

    Constraints (§9):
    - New templates are tenant-owned and ``editing_mode=raw``.
    - Re-push goes through ``save_template_version`` (appends TemplateVersion).
    - Field loss on published content requires ``allow_field_loss=true``.
    - ``if_match`` (html etag) guards concurrent writes; orthogonal to field loss.
    """
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)

    tenant = scope.tenant
    html = html if isinstance(html, str) else ""
    if not html.strip():
        return tool_error("html is required.")

    page_slug = (page or "").strip().lower() or None
    title = (title or "").strip()
    label = "MCP push_page"

    explicit_tpl: Optional[Template] = None
    if template_id is not None:
        explicit_tpl = Template.objects.filter(pk=template_id).first()
        if explicit_tpl is None or explicit_tpl.tenant_id != tenant.pk:
            return _TEMPLATE_DENIED

    if page_slug is not None and page_slug in RESERVED_PAGE_SLUGS:
        return tool_error(
            f"Slug '{page_slug}' is reserved by the CMS and cannot be used "
            f"as a page path."
        )

    # ---- Home (page omitted) ----
    if page_slug is None:
        if explicit_tpl is not None:
            tpl = explicit_tpl
        else:
            try:
                tpl = _ensure_tenant_owned_template(
                    tenant, tenant.template, user=auth.user
                )
            except LookupError:
                return _TEMPLATE_DENIED
            if tpl.pk != tenant.template_id:
                tenant.template = tpl
                tenant.save(update_fields=["template", "updated_at"])

        err, result = _push_html_onto_template(
            tpl,
            html,
            user=auth.user,
            allow_field_loss=allow_field_loss,
            if_match=if_match,
            label=label,
        )
        if err is not None:
            return err
        return tool_success(
            {
                "url": tenant_canonical_public_url(tenant),
                "etag": content_mod.html_etag(result.template.html_source),
                "page": None,
                "template_id": result.template.pk,
                "version": result.version.number,
                "editing_mode": result.template.editing_mode,
            }
        )

    # ---- Inner page ----
    existing = (
        Page.objects.filter(tenant=tenant, slug=page_slug)
        .select_related("template")
        .first()
    )
    page_title = title or page_slug.replace("-", " ").title()

    if existing is None:
        with transaction.atomic():
            if explicit_tpl is not None:
                tpl = explicit_tpl
                err, result = _push_html_onto_template(
                    tpl,
                    html,
                    user=auth.user,
                    allow_field_loss=allow_field_loss,
                    if_match=if_match,
                    label=label,
                )
                if err is not None:
                    transaction.set_rollback(True)
                    return err
            else:
                tpl = Template(
                    name=page_title[:120],
                    html_source="<!--pending push_page-->",
                    tenant=tenant,
                    editing_mode=Template.EDITING_RAW,
                )
                tpl.save()
                err, result = _push_html_onto_template(
                    tpl,
                    html,
                    user=auth.user,
                    allow_field_loss=allow_field_loss,
                    if_match=None,  # new row; no concurrent peer yet
                    label=label,
                )
                if err is not None:
                    transaction.set_rollback(True)
                    return err

            page_row = Page.objects.create(
                tenant=tenant,
                template=result.template,
                title=page_title[:120],
                slug=page_slug,
                content={},
                is_published=False,
            )

        return tool_success(
            {
                "url": tenant_canonical_public_url(tenant, page_slug=page_row.slug),
                "etag": content_mod.html_etag(result.template.html_source),
                "page": page_row.slug,
                "template_id": result.template.pk,
                "version": result.version.number,
                "editing_mode": result.template.editing_mode,
            }
        )

    # Re-push existing page.
    if explicit_tpl is not None:
        tpl = explicit_tpl
    else:
        try:
            tpl = _ensure_tenant_owned_template(
                tenant, existing.template, user=auth.user
            )
        except LookupError:
            return _TEMPLATE_DENIED

    if tpl.pk != existing.template_id:
        existing.template = tpl
        existing.save(update_fields=["template", "updated_at"])

    err, result = _push_html_onto_template(
        tpl,
        html,
        user=auth.user,
        allow_field_loss=allow_field_loss,
        if_match=if_match,
        label=label,
    )
    if err is not None:
        return err

    if title:
        existing.title = title[:120]
        existing.save(update_fields=["title", "updated_at"])

    return tool_success(
        {
            "url": tenant_canonical_public_url(tenant, page_slug=existing.slug),
            "etag": content_mod.html_etag(result.template.html_source),
            "page": existing.slug,
            "template_id": result.template.pk,
            "version": result.version.number,
            "editing_mode": result.template.editing_mode,
        }
    )


# list_sites returns a plain dict; wrap at call site for MCP envelope.
HANDLERS: dict[str, Callable[..., Any]] = {
    "list_sites": list_sites,
    "list_pages": list_pages,
    "get_page": get_page,
    "get_content": get_content,
    "create_client_account": create_client_account,
    "patch_content": patch_content,
    "push_page": push_page,
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
    {
        "name": "patch_content",
        "description": (
            "Write one home-page field by dotted id. Requires a current "
            "if_match etag from get_content/get_page. Inner pages are not "
            "supported until page versioning exists."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["site", "field", "value", "if_match"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "field": {"type": "string"},
                "value": {},
                "if_match": {"type": "string"},
                "page": {
                    "type": ["string", "null"],
                    "description": "Must be omitted/null (home only)",
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["etag", "url"],
            "additionalProperties": False,
            "properties": {
                "etag": {"type": "string"},
                "url": {"type": "string"},
            },
        },
        "annotations": WRITE_ANNOTATIONS,
    },
    {
        "name": "push_page",
        "description": (
            "Create or replace a page's HTML for a site. Omit page (or pass null) "
            "to push the site home. New pages get a tenant-owned template in "
            "editing_mode=raw. Re-pushes append a TemplateVersion. Use if_match "
            "(html etag from a prior push) to guard concurrent writes; use "
            "allow_field_loss=true when a re-push drops fields a published page "
            "still holds. Reserved slugs (privacy, terms, …) are refused."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["site", "html"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string", "description": "Tenant subdomain"},
                "html": {
                    "type": "string",
                    "description": "Full page HTML to store as the template source",
                },
                "page": {
                    "type": ["string", "null"],
                    "description": "Page slug; omit/null for home",
                },
                "title": {
                    "type": "string",
                    "description": "Page title (create / optional update)",
                },
                "allow_field_loss": {
                    "type": "boolean",
                    "description": (
                        "Confirm dropping fields that published content still uses"
                    ),
                },
                "if_match": {
                    "type": "string",
                    "description": "Current html etag; required to avoid clobbering",
                },
                "template_id": {
                    "type": "integer",
                    "description": (
                        "Optional tenant-owned template pk; foreign/missing ids "
                        "are refused indistinguishably"
                    ),
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "required": [
                "url",
                "etag",
                "page",
                "template_id",
                "version",
                "editing_mode",
            ],
            "additionalProperties": False,
            "properties": {
                "url": {"type": "string"},
                "etag": {"type": "string"},
                "page": {"type": ["string", "null"]},
                "template_id": {"type": "integer"},
                "version": {"type": "integer"},
                "editing_mode": {"type": "string"},
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
        if key not in arguments:
            return f"Missing required argument '{key}'"
        if arguments[key] in (None, "") and key != "value":
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
    if "page" in args and args["page"] is None:
        args.pop("page")
    if "custom_domain" in args and args["custom_domain"] in (None, ""):
        args.pop("custom_domain")
    if "title" in args and args["title"] in (None, ""):
        args.pop("title")
    if "if_match" in args and args["if_match"] in (None, ""):
        args.pop("if_match")
    if "template_id" in args and args["template_id"] is None:
        args.pop("template_id")
    if "allow_field_loss" in args and args["allow_field_loss"] is None:
        args.pop("allow_field_loss")
    return handler(auth, **args), None
