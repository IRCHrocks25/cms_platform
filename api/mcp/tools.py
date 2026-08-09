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
_PUBLISH_DENIED = _CREATE_DENIED
_DELETE_DENIED = _CREATE_DENIED


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
        {"fields": fields, "content_etag": content_mod.content_etag(stored)}
    )


def get_page_html(
    auth: ResolvedAuth,
    *,
    site: str,
    page: Optional[str] = None,
    version: Optional[int] = None,
) -> dict[str, Any]:
    """CMS-30: return template HTML (latest or a specific TemplateVersion).

    ``etag`` is always ``html_etag`` of the *live* ``template.html_source`` —
    the same value ``push_page`` compares against ``if_match``. Historical
    pulls still return that current etag so a restore-style re-push works.
    """
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        editable, _schema, _stored = content_mod.resolve_editable(
            scope.tenant, page
        )
    except LookupError:
        return page_access_denied(site, page or "")

    template = editable.template
    current_etag = content_mod.html_etag(template.html_source)
    page_slug = None if isinstance(editable, Tenant) else editable.slug

    if version is None:
        latest = template.versions.order_by("-number").first()
        html = template.html_source
        number = latest.number if latest is not None else 1
    else:
        row = template.versions.filter(number=version).first()
        if row is None:
            return page_access_denied(site, page or "")
        html = row.html_source
        number = row.number

    return tool_success(
        {
            "html_source": html,
            "version": number,
            "etag": current_etag,
            "page": page_slug,
            "template_id": template.pk,
            "editing_mode": template.editing_mode,
        }
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
            "content_etag": content_mod.content_etag(stored),
        }
    )


def _public_site_url(subdomain: str) -> str:
    """Build the new site's public URL from TENANT_BASE_DOMAIN (never hardcode)."""
    base = (settings.TENANT_BASE_DOMAIN or "").strip(".").lower()
    if not base or base == "localhost" or base.endswith(".local"):
        return f"http://{subdomain}.{base or 'localhost'}/"
    return f"https://{subdomain}.{base}/"


def _content_still_template_defaults(tenant: Tenant) -> bool:
    """True when stored content equals the template's schema defaults.

    Fresh ``create_client_account`` sites seed ``tenant.content`` from
    defaults, so every field is *present* in storage (``is_default`` is
    False). Comparing the blobs catches the "never edited" case the
    publish guard is meant for.
    """
    defaults = (tenant.template.schema or {}).get("defaults") or {}
    content = tenant.content or {}

    def _public(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            key: _public(child)
            for key, child in value.items()
            if not str(key).startswith("_")
        }

    public_defaults = _public(defaults)
    public_content = _public(content)
    if not public_defaults:
        # No editable defaults to compare — treat empty content as untouched.
        return public_content == {} or public_content == public_defaults
    return public_content == public_defaults


def publish_site(
    auth: ResolvedAuth,
    *,
    site: str,
    force: bool = False,
) -> dict[str, Any]:
    """CMS-31: flip a tenant live. Superuser only; no unpublish counterpart."""
    if not getattr(auth.user, "is_superuser", False):
        return _PUBLISH_DENIED

    site = (site or "").strip().lower()
    if not site:
        return tool_error("site is required.")

    tenant = Tenant.objects.filter(subdomain=site).select_related("template").first()
    if tenant is None:
        return _PUBLISH_DENIED

    html = (tenant.template.html_source or "").strip()
    if not html and not force:
        return tool_error(
            "Site has no HTML yet. Push a page first, or pass force=true "
            "to publish anyway."
        )

    if _content_still_template_defaults(tenant) and not force:
        return tool_error(
            "Site content is still the template defaults. Edit content "
            "first, or pass force=true to publish anyway."
        )

    if not tenant.is_published:
        tenant.is_published = True
        tenant.save(update_fields=["is_published", "updated_at"])

    return tool_success(
        {
            "url": _public_site_url(tenant.subdomain),
            "published": True,
            "subdomain": tenant.subdomain,
        }
    )


def publish_page(
    auth: ResolvedAuth,
    *,
    site: str,
    page: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """CMS-34: flip an inner Page live. Superuser only; no unpublish counterpart.

    ``publish_site`` only flips ``Tenant.is_published`` — nothing sets
    ``Page.is_published``, so a page created by ``push_page`` 404s forever
    unless this is called. A page's public reachability
    (``core.views.page_render`` / ``page_render_public``) gates solely on
    ``Page.is_published``, independent of the tenant's ``is_published`` — so
    this does not also require the site to be published first.
    """
    if not getattr(auth.user, "is_superuser", False):
        return _PUBLISH_DENIED

    site = (site or "").strip().lower()
    page = (page or "").strip().lower()
    if not site:
        return tool_error("site is required.")
    if not page:
        return tool_error(
            "page is required to publish an inner page. Use publish_site "
            "to publish the site home."
        )

    tenant = Tenant.objects.filter(subdomain=site).first()
    if tenant is None:
        return _PUBLISH_DENIED

    page_row = (
        Page.objects.filter(tenant=tenant, slug=page)
        .select_related("template")
        .first()
    )
    if page_row is None:
        return _PUBLISH_DENIED

    html = (page_row.template.html_source or "").strip()
    if not html and not force:
        return tool_error(
            "Page has no HTML yet. Push it with push_page first, or pass "
            "force=true to publish anyway."
        )

    if not page_row.is_published:
        page_row.is_published = True
        page_row.save(update_fields=["is_published", "updated_at"])

    return tool_success(
        {
            "url": tenant_canonical_public_url(tenant, page_slug=page_row.slug),
            "published": True,
            "site": tenant.subdomain,
            "page": page_row.slug,
        }
    )


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
    return tool_success({"content_etag": new_etag, "url": url})


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


def _template_still_needed(template: Template) -> bool:
    """True if the tenant home, a clone lineage, or another page still needs it.

    Call after the page being deleted is already gone, so the check reflects
    only *other* referrers: ``Tenant.template`` (the site home),
    ``Template.cloned_from`` (a clone's lineage pointer), or any remaining
    ``Page.template``.
    """
    if Tenant.objects.filter(template_id=template.pk).exists():
        return True
    if Template.objects.filter(cloned_from_id=template.pk).exists():
        return True
    if Page.objects.filter(template_id=template.pk).exists():
        return True
    return False


def delete_page(
    auth: ResolvedAuth,
    *,
    site: str,
    page: str = "",
) -> dict[str, Any]:
    """CMS-36: remove an inner Page and its now-orphaned tenant-owned template.

    A page created by ``push_page`` owns its Template 1:1 in the common
    case, so deleting only the Page row would orphan that Template (and its
    TemplateVersion history) forever. Deletes ``core_page`` first, then
    ``core_template`` if nothing else still needs it — ``TemplateVersion``
    cascades automatically off the Template FK (``on_delete=CASCADE``), no
    manual step required. Kept, not deleted, when the template is still the
    site home (``Tenant.template``), a clone's lineage (``cloned_from``), or
    used by another Page. One transaction. Refuses outright when ``page`` is
    omitted/null — the site home isn't a Page row and can't be deleted here.
    """
    if not getattr(auth.user, "is_superuser", False):
        return _DELETE_DENIED

    site = (site or "").strip().lower()
    page = (page or "").strip().lower()
    if not site:
        return tool_error("site is required.")
    if not page:
        return tool_error(
            "Cannot delete the site home — it isn't a Page row. delete_page "
            "only removes additional pages created by push_page."
        )

    tenant = Tenant.objects.filter(subdomain=site).first()
    if tenant is None:
        return _DELETE_DENIED

    page_row = (
        Page.objects.filter(tenant=tenant, slug=page)
        .select_related("template")
        .first()
    )
    if page_row is None:
        return _DELETE_DENIED

    template = page_row.template
    with transaction.atomic():
        page_row.delete()
        template_deleted = not _template_still_needed(template)
        if template_deleted:
            template.delete()

    return tool_success(
        {
            "site": tenant.subdomain,
            "page": page,
            "deleted": True,
            "template_deleted": template_deleted,
        }
    )


# list_sites returns a plain dict; wrap at call site for MCP envelope.
HANDLERS: dict[str, Callable[..., Any]] = {
    "list_sites": list_sites,
    "list_pages": list_pages,
    "get_page": get_page,
    "get_page_html": get_page_html,
    "get_content": get_content,
    "create_client_account": create_client_account,
    "publish_site": publish_site,
    "publish_page": publish_page,
    "patch_content": patch_content,
    "push_page": push_page,
    "delete_page": delete_page,
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
            "required": ["fields", "content_etag"],
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
                "content_etag": {
                    "type": "string",
                    "description": (
                        "SHA-256 of the stored content blob (not HTML). "
                        "Use with patch_content if_match — not push_page."
                    ),
                },
            },
        },
        "annotations": ANNOTATIONS,
    },
    {
        "name": "get_page_html",
        "description": (
            "Read a page's template HTML source (latest, or a specific "
            "TemplateVersion via version=). Returns the live html etag that "
            "push_page compares against if_match — even when pulling an older "
            "version — so a restore-style re-push works. For field values use "
            "get_page / get_content instead."
        ),
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
                "version": {
                    "type": "integer",
                    "description": (
                        "Optional TemplateVersion number; omit for latest"
                    ),
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "required": [
                "html_source",
                "version",
                "etag",
                "page",
                "template_id",
                "editing_mode",
            ],
            "additionalProperties": False,
            "properties": {
                "html_source": {"type": "string"},
                "version": {"type": "integer"},
                "etag": {
                    "type": "string",
                    "description": (
                        "SHA-256 of the live template HTML — pass to "
                        "push_page if_match"
                    ),
                },
                "page": {"type": ["string", "null"]},
                "template_id": {"type": "integer"},
                "editing_mode": {"type": "string"},
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
            "required": ["value", "is_default", "content_etag"],
            "additionalProperties": False,
            "properties": {
                "value": {},
                "is_default": {"type": "boolean"},
                "content_etag": {
                    "type": "string",
                    "description": (
                        "SHA-256 of the stored content blob (not HTML). "
                        "Use with patch_content if_match — not push_page."
                    ),
                },
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
        "name": "publish_site",
        "description": (
            "Publish a site to the public internet. Superuser only. Sites "
            "created via create_client_account start unpublished — call this "
            "after content is ready. Refuses when every editable field is "
            "still the template default (or HTML is empty) unless force=true. "
            "There is no unpublish tool; take a site offline from the dashboard."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["site"],
            "additionalProperties": False,
            "properties": {
                "site": {
                    "type": "string",
                    "description": "Tenant subdomain to publish",
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "Bypass the empty-HTML / still-defaults guard"
                    ),
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["url", "published", "subdomain"],
            "additionalProperties": False,
            "properties": {
                "url": {"type": "string"},
                "published": {"type": "boolean"},
                "subdomain": {"type": "string"},
            },
        },
        "annotations": WRITE_ANNOTATIONS,
    },
    {
        "name": "publish_page",
        "description": (
            "Publish an inner page to the public internet. Superuser only. "
            "Pages created via push_page start unpublished — publish_site "
            "only flips the tenant, never a Page — so call this after the "
            "page has content. Refuses when the page's template has no HTML "
            "yet unless force=true. Independent of the site's publish state: "
            "a page's own URL is reachable once published even if the site "
            "home isn't. There is no unpublish counterpart — same as "
            "publish_site."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["site"],
            "additionalProperties": False,
            "properties": {
                "site": {
                    "type": "string",
                    "description": "Tenant subdomain",
                },
                "page": {
                    "type": ["string", "null"],
                    "description": (
                        "Page slug to publish. Required — there is no "
                        "site-home shortcut; use publish_site for that."
                    ),
                },
                "force": {
                    "type": "boolean",
                    "description": "Bypass the empty-HTML guard",
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["url", "published", "site", "page"],
            "additionalProperties": False,
            "properties": {
                "url": {"type": "string"},
                "published": {"type": "boolean"},
                "site": {"type": "string"},
                "page": {"type": "string"},
            },
        },
        "annotations": WRITE_ANNOTATIONS,
    },
    {
        "name": "patch_content",
        "description": (
            "Write one home-page field by dotted id. Requires a current "
            "if_match content_etag from get_content/get_page. Inner pages are "
            "not supported until page versioning exists."
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
            "required": ["content_etag", "url"],
            "additionalProperties": False,
            "properties": {
                "content_etag": {"type": "string"},
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
            "(html etag from get_page_html or a prior push) to guard concurrent "
            "writes; use "
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
    {
        "name": "delete_page",
        "description": (
            "Delete an inner page created by push_page, and its now-orphaned "
            "tenant-owned template (with that template's version history). "
            "Superuser only. The template is kept — only the page is removed "
            "— when it's still used by the site home, a template clone, or "
            "another page. Refuses to delete the site home; that isn't a "
            "Page row (use the dashboard to take a whole site down)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["site"],
            "additionalProperties": False,
            "properties": {
                "site": {
                    "type": "string",
                    "description": "Tenant subdomain",
                },
                "page": {
                    "type": ["string", "null"],
                    "description": (
                        "Page slug to delete. Required — omitting it "
                        "(the site home) is refused."
                    ),
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["site", "page", "deleted", "template_deleted"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "page": {"type": "string"},
                "deleted": {"type": "boolean"},
                "template_deleted": {"type": "boolean"},
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
    if name in (
        "create_client_account",
        "publish_site",
        "publish_page",
        "delete_page",
    ) and not getattr(auth.user, "is_superuser", False):
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
    if "version" in args and args["version"] is None:
        args.pop("version")
    if "force" in args and args["force"] is None:
        args.pop("force")
    return handler(auth, **args), None
