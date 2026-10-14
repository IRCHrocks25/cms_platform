"""MCP tool definitions and handlers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Optional
from bs4 import BeautifulSoup

from django.conf import settings
from django.core.exceptions import ValidationError

from django.db import transaction

from core.models import CustomDomain, Page, RESERVED_PAGE_SLUGS, Template, Tenant
from core.parser import build_schema
from core.renderer import strip_defaults
from core.services import content_versions as cv
from core.services import custom_domains as custom_domains_svc
from core.services import ghl_embed_slots, ghl_forms
from core.services import blocks
from core.services.template_sanitizer import sanitize_template_html
from core.services.accounts import (
    AccountSeedError,
    CustomDomainError,
    create_tenant_account,
)
from core.services.templates import (
    ConcurrentWriteError,
    FieldDriftError,
    FieldLossError,
    save_template_version,
)
from core.tenant_hosts import tenant_host_label
from core.urls_helpers import tenant_canonical_public_url

from api.auth import ResolvedAuth, TenantScope
from api.service_auth import ServicePrincipal
from api.mcp import content as content_mod
from api.mcp import structure as structure_mod
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
_LIST_TEMPLATES_DENIED = _CREATE_DENIED
_CUSTOM_DOMAIN_DENIED = _CREATE_DENIED


def _site_row(tenant: Tenant, role: str) -> dict[str, Any]:
    return {
        "site_id": str(tenant.pk),
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


def get_connection_context(auth: ResolvedAuth) -> dict[str, Any]:
    """Return stable actor and membership IDs for an external tenant binding.

    Platform-wide superusers are deliberately refused. External connections
    must be backed by a membership-scoped principal whose current site set can
    be pinned and checked for drift by the caller.
    """
    if auth.platform_role or getattr(auth.user, "is_superuser", False):
        return _CREATE_DENIED
    return tool_success(
        {
            "principal_id": str(auth.user.pk),
            "sites": [
                _site_row(scope.tenant, scope.role)
                for scope in auth.tenant_scopes
            ],
        }
    )


def _require_scope(auth: ResolvedAuth, site: str) -> Optional[TenantScope]:
    if isinstance(auth, ServicePrincipal):
        if site != auth.tenant.subdomain:
            return None
        return auth.for_tenant(auth.tenant)
    tenant = Tenant.objects.filter(subdomain=site).first()
    if tenant is None:
        return None
    return auth.for_tenant(tenant)


def _current_schema(editable, stored_schema: dict[str, Any]) -> dict[str, Any]:
    """Reparse annotations so older stored schemas cannot bypass new rules."""
    template = editable.template
    if template and template.html_source:
        return build_schema(template.html_source)
    return stored_schema


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


def list_blocks(
    auth: ResolvedAuth, *, site: str, page: Optional[str] = None
) -> dict[str, Any]:
    """List every stored block instance in region order, including fieldless ones."""
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        editable, _schema, stored = content_mod.resolve_editable(scope.tenant, page)
    except LookupError:
        return page_access_denied(site, page or "")
    try:
        shell = BeautifulSoup(editable.template.html_source or "", "html.parser")
        shell_regions = [
            (slot.get("data-region") or "main").strip() or "main"
            for slot in shell.find_all(attrs={"data-region": True})
        ]
        rows = content_mod.list_block_instances(
            stored, blocks.catalog_for_template(editable.template),
            region_order=shell_regions,
        )
    except ValueError as exc:
        return tool_error(str(exc))
    return tool_success({
        "page": None if isinstance(editable, Tenant) else editable.slug,
        "blocks": rows,
        "content_etag": content_mod.content_etag(stored),
    })


def get_page(
    auth: ResolvedAuth, *, site: str, page: Optional[str] = None
) -> dict[str, Any]:
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        editable, schema, stored = content_mod.resolve_editable(
            scope.tenant, page
        )
    except LookupError:
        return page_access_denied(site, page or "")
    try:
        fields = content_mod.read_fields(
            _current_schema(editable, schema), stored,
            blocks.catalog_for_template(editable.template) if editable.template.is_block_shell else None,
        )
    except ValueError as exc:
        return tool_error(str(exc))
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
        editable, schema, stored = content_mod.resolve_editable(
            scope.tenant, page
        )
    except LookupError:
        return page_access_denied(site, page or "")
    try:
        value, is_default = content_mod.read_field(
            _current_schema(editable, schema), stored, field,
            blocks.catalog_for_template(editable.template) if editable.template.is_block_shell else None,
        )
    except (KeyError, ValueError):
        return tool_error(f"Unknown field '{field}'.")
    return tool_success(
        {
            "value": value,
            "is_default": is_default,
            "content_etag": content_mod.content_etag(stored),
        }
    )


def _block_fragment(editable, stored, instance_id):
    """Return the rendered, instance-scoped fragment (nested children included)."""
    catalog = blocks.catalog_for_template(editable.template)
    instance, block = content_mod.block_instances(stored, catalog)[instance_id]
    html = blocks.render_content(editable.template, stored)
    soup = BeautifulSoup(html, "lxml")
    wrapper = soup.find(attrs={"data-instance-id": instance_id})
    if wrapper is None:
        raise KeyError(instance_id)
    return str(wrapper), instance, block, catalog


def get_block_html(auth: ResolvedAuth, *, site: str, instance_id: str,
                   page: Optional[str] = None) -> dict[str, Any]:
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        editable, _schema, stored = content_mod.resolve_editable(scope.tenant, page)
        if not editable.template.is_block_shell:
            raise KeyError(instance_id)
        html, _instance, _block, _catalog = _block_fragment(editable, stored, instance_id)
    except (LookupError, KeyError, ValueError):
        return tool_error("Block instance is unavailable on this page.")
    return tool_success({"html": html, "instance_id": instance_id,
                         "content_etag": content_mod.content_etag(stored)})


def _changed_block_text(current_html, submitted_html, instance_id, block):
    """Accept only edits to text/richtext descendants owned by this instance."""
    current = BeautifulSoup(current_html, "lxml")
    submitted = BeautifulSoup(submitted_html, "lxml")
    current_root = current.find(attrs={"data-instance-id": instance_id})
    submitted_root = submitted.find(attrs={"data-instance-id": instance_id})
    if submitted_root is None or len(submitted.find_all(attrs={"data-instance-id": instance_id})) != 1:
        raise ValueError("HTML must contain exactly the requested block instance.")
    if len([node for node in submitted.body.contents if str(node).strip()]) != 1:
        raise ValueError("HTML must contain only the requested block instance.")
    schema_fields = {str(f.get("id")): f for f in (block.get("schema") or {}).get("fields") or []}
    changes = {}
    for name, root in (("current", current_root), ("submitted", submitted_root)):
        editable = {}
        for el in root.find_all(attrs={"data-edit": True}):
            if el.find_parent(attrs={"data-instance-id": True}) is not root:
                continue
            field_id = str(el.get("data-edit"))
            prefix = f"{instance_id}."
            if not field_id.startswith(prefix) or field_id[len(prefix):] not in schema_fields or field_id in editable:
                raise ValueError("HTML contains an unknown or repeated editable field.")
            editable[field_id] = el
        if name == "current":
            expected_ids = set(editable)
        elif set(editable) != expected_ids:
            raise ValueError("HTML must preserve every editable field.")
        for field_id, el in editable.items():
            field_name = field_id[len(instance_id) + 1:]
            field_type = schema_fields[field_name].get("type")
            if field_type not in {"text", "richtext"}:
                continue
            if name == "submitted":
                changes[field_id] = (
                    el.get_text() if field_type == "text"
                    else sanitize_template_html(el.decode_contents())
                )
            el.clear()
            el.string = "__CMS_EDITABLE_VALUE__"
    if str(current_root) != str(submitted_root):
        raise ValueError("Block HTML may change text and richtext values only; structure and attributes must match.")
    return changes


@transaction.atomic
def push_block_html(auth: ResolvedAuth, *, site: str, instance_id: str, html: str,
                    if_match: str, page: Optional[str] = None) -> dict[str, Any]:
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        editable, schema, stored = content_mod.resolve_editable(scope.tenant, page)
    except LookupError:
        return page_access_denied(site, page or "")
    editable = type(editable).objects.select_for_update().get(pk=editable.pk)
    stored = editable.content or {}
    if not editable.template.is_block_shell:
        return tool_error("This page is not block-backed.")
    if not editable.template.is_client_editable and not (
        getattr(auth.user, "is_superuser", False) or getattr(auth.user, "is_staff", False)
    ):
        return tool_error("This site isn't set up for editing yet — contact your agency.")
    if content_mod.content_etag(stored) != if_match:
        return tool_conflict("Conflict (409): content changed; re-read the block and retry.")
    try:
        current_html, _instance, block, catalog = _block_fragment(editable, stored, instance_id)
        changes = _changed_block_text(current_html, html, instance_id, block)
        proposed = stored
        for field_id, value in changes.items():
            proposed = content_mod.write_block_field(proposed, catalog, field_id, value)
        ghl_embed_slots.validate_embed_content_update(
            tenant=scope.tenant, schema=_current_schema(editable, schema),
            current_content=stored, new_content=proposed,
            is_published=editable.is_published, catalog=catalog,
        )
    except (KeyError, ValueError) as exc:
        return tool_error(str(exc))
    cv.save_editable_content(editable, proposed, user=auth.user, source=cv.SOURCE_MCP)
    editable.refresh_from_db()
    return tool_success({"instance_id": instance_id,
                         "content_etag": content_mod.content_etag(editable.content or {})})


def list_embed_slots(
    auth: ResolvedAuth, *, site: str, page: Optional[str] = None
) -> dict[str, Any]:
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        editable, schema, stored = content_mod.resolve_editable(scope.tenant, page)
    except LookupError:
        return page_access_denied(site, page or "")
    schema = _current_schema(editable, schema)
    return tool_success(
        {
            "site": scope.tenant.subdomain,
            "page": None if isinstance(editable, Tenant) else editable.slug,
            "published": editable.is_published,
            "slots": ghl_embed_slots.get_embed_slots(
                schema, stored,
                catalog=blocks.catalog_for_template(editable.template)
                if editable.template.is_block_shell else None,
            ),
            "content_etag": content_mod.content_etag(stored),
        }
    )


def list_ghl_forms(auth: ResolvedAuth, *, site: str) -> dict[str, Any]:
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        forms = ghl_forms.list_forms_for_tenant(scope.tenant)
    except ghl_forms.GhlFormsUnavailable as exc:
        return tool_error(exc.public_message)
    return tool_success(
        {
            "site": scope.tenant.subdomain,
            "forms": [
                {**form, "value": f'form:{form["id"]}'} for form in forms
            ],
        }
    )


@transaction.atomic
def set_embed_slot(
    auth: ResolvedAuth,
    *,
    site: str,
    field: str,
    value: str,
    if_match: str,
    page: Optional[str] = None,
) -> dict[str, Any]:
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        editable, schema, stored = content_mod.resolve_editable(scope.tenant, page)
    except LookupError:
        return page_access_denied(site, page or "")
    schema = _current_schema(editable, schema)
    editable = type(editable).objects.select_for_update().get(pk=editable.pk)
    stored = editable.content or {}
    catalog = blocks.catalog_for_template(editable.template) if editable.template.is_block_shell else None

    template = getattr(editable, "template", None)
    if (template is None or not template.is_client_editable) and not (
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
            "Re-read with list_embed_slots and retry."
        )

    slots = {
        slot["id"]: slot
        for slot in ghl_embed_slots.get_embed_slots(schema, stored, catalog=catalog)
    }
    slot = slots.get(field)
    if slot is None or slot["kind"] != "form":
        return tool_error(f"Unknown GHL form embed slot '{field}'.")
    if value == slot["value"]:
        return tool_success(
            {
                "site": scope.tenant.subdomain,
                "page": None if isinstance(editable, Tenant) else editable.slug,
                "field": field,
                "value": value,
                "content_etag": current_etag,
            }
        )

    try:
        new_content = (
            content_mod.write_block_field(stored, catalog, field, value)
            if catalog and field.split(".", 1)[0] in content_mod.block_instances(stored, catalog)
            else content_mod.write_field(stored, field, value)
        )
        ghl_embed_slots.validate_embed_content_update(
            tenant=scope.tenant,
            schema=schema,
            current_content=stored,
            new_content=new_content,
            is_published=editable.is_published,
            catalog=catalog,
        )
    except ValueError as exc:
        return tool_error(str(exc))

    cv.save_editable_content(editable, new_content, user=auth.user, source=cv.SOURCE_MCP)
    editable.refresh_from_db()
    return tool_success(
        {
            "site": scope.tenant.subdomain,
            "page": None if isinstance(editable, Tenant) else editable.slug,
            "field": field,
            "value": value,
            "content_etag": content_mod.content_etag(editable.content or {}),
        }
    )


def _public_site_url(subdomain: str) -> str:
    """Build the new site's public URL from TENANT_BASE_DOMAIN (never hardcode)."""
    base = (settings.TENANT_BASE_DOMAIN or "").strip(".").lower()
    if not base or base == "localhost" or base.endswith(".local"):
        return f"http://{subdomain}.{base or 'localhost'}/"
    return f"https://{tenant_host_label(subdomain)}.{base}/"


#: Meta namespaces that change what a visitor sees: per-element styling, hidden
#: sections, brand tokens, global typography. Editing only these is a real
#: customization — comparable products treat a design change as a change — so
#: the publish gate must not call such a site untouched.
_VISIBLE_META_KEYS = ("_styles", "_hidden", "_tokens", "_global")


def _copy_still_template_defaults(tenant: Tenant) -> bool:
    """True when no editable *copy* differs from the template.

    Content is stored sparse — only values that differ from the template
    default (``renderer.strip_defaults``) — so this is "nothing left once the
    defaults are stripped". Running the strip here rather than trusting the
    stored shape also covers rows written before sparse content shipped, which
    still carry a full copy of the defaults.
    """
    schema = tenant.template.schema or {}
    content = tenant.content or {}

    def _public(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            key: _public(child)
            for key, child in value.items()
            if not str(key).startswith("_")
        }

    authored = strip_defaults(schema, _public(content))
    return not any(bool(fields) for fields in authored.values())


def _has_visible_meta(tenant: Tenant) -> bool:
    content = tenant.content or {}
    return any(bool(content.get(key)) for key in _VISIBLE_META_KEYS)


def _content_still_template_defaults(tenant: Tenant) -> bool:
    """True when nothing about this site has been customized at all.

    Copy *and* design. A site whose colors, fonts, brand tokens, or visible
    sections were changed is customized even if every word is still the
    template's — see ``docs/research/2026-08-27-cms-content-damage-fixes.md``
    for the survey this follows. The narrower "the copy is still boilerplate"
    signal is ``_copy_still_template_defaults``, which publish reports as a
    warning rather than a refusal.
    """
    return _copy_still_template_defaults(tenant) and not _has_visible_meta(tenant)


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

    payload = {
        "url": _public_site_url(tenant.subdomain),
        "published": True,
        "subdomain": tenant.subdomain,
    }
    # Design changed, copy did not. Worth saying — restyling does not remove
    # another company's name, placeholder claims, or unlicensed sample images —
    # but not worth blocking a deliberately restyled site over.
    if _copy_still_template_defaults(tenant):
        payload["warning"] = (
            "Published, but every editable copy field is still the template's. "
            "Check the wording before sharing this URL."
        )
    return tool_success(payload)


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


def _template_row(template: Template) -> dict[str, Any]:
    return {
        "id": template.pk,
        "name": template.name,
        "slug": template.slug,
        "description": template.description,
        "editing_mode": template.editing_mode,
    }


def list_templates(auth: ResolvedAuth) -> dict[str, Any]:
    """CMS-35: list agency-library templates. Superuser only — this exists to
    feed create_client_account's template_id, which is itself superuser-only
    and library-only (CMS-27 locked template assignment to `tenant IS NULL`
    rows). No non-superuser principal can call create_client_account, so
    there is no legitimate reason to expose the library to them either.
    """
    if not getattr(auth.user, "is_superuser", False):
        return _LIST_TEMPLATES_DENIED
    templates = Template.objects.filter(tenant__isnull=True).order_by("name")
    return tool_success({"templates": [_template_row(t) for t in templates]})


def create_client_account(
    auth: ResolvedAuth,
    *,
    name: str,
    subdomain: str,
    username: str,
    email: str,
    template_id: Optional[int] = None,
    html: Optional[str] = None,
    custom_domain: str = "",
) -> dict[str, Any]:
    """CMS-11 + CMS-8: mint tenant + owner login; password returned once only.

    Exactly one of ``template_id`` (library clone) or ``html`` (tenant-owned
    raw template) must be supplied (CMS-38). ``custom_domain``, if given,
    becomes a real unverified CustomDomain row (CMS-37) — see the tool
    description for what the caller still has to do.
    """
    if not getattr(auth.user, "is_superuser", False):
        return _CREATE_DENIED

    subdomain = (subdomain or "").strip().lower()
    username = (username or "").strip()
    email = (email or "").strip()
    name = (name or "").strip()
    custom_domain = (custom_domain or "").strip().lower()
    if not name or not subdomain or not username:
        return tool_error("name, subdomain, and username are required.")

    has_template = template_id is not None
    has_html = html is not None
    if has_template == has_html:
        return tool_error(
            "Provide exactly one of template_id or html "
            "(not both, and not neither)."
        )

    html = (html or "").strip() or None
    if has_html and html is None:
        return tool_error(
            "Provide exactly one of template_id or html "
            "(not both, and not neither)."
        )

    reserved = getattr(settings, "TENANT_RESERVED_SUBDOMAINS", set()) or set()
    if subdomain in reserved:
        return tool_error(f"Subdomain '{subdomain}' is reserved.")
    if Tenant.objects.filter(subdomain=subdomain).exists():
        return tool_error(f"Subdomain '{subdomain}' is already taken.")

    from django.contrib.auth import get_user_model

    User = get_user_model()
    if User.objects.filter(username__iexact=username).exists():
        return tool_error(f"Username '{username}' is already taken.")

    template = None
    if has_template:
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
            html=html,
            username=username,
            email=email,
            is_published=False,
        )
    except AccountSeedError as exc:
        return tool_error(str(exc))
    except CrossTenantTemplateError as exc:
        return tool_error(str(exc))
    except CustomDomainError as exc:
        return tool_error(str(exc))
    except ValidationError as exc:
        return tool_error(" ".join(exc.messages))

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


@transaction.atomic
def patch_content(
    auth: ResolvedAuth,
    *,
    site: str,
    field: str,
    value: Any,
    if_match: str,
    page: Optional[str] = None,
) -> dict[str, Any]:
    """Write a field with content-etag concurrency and versioned undo."""
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)

    try:
        editable, schema, stored = content_mod.resolve_editable(
            scope.tenant, page
        )
    except LookupError:
        return page_access_denied(site, page or "")
    schema = _current_schema(editable, schema)
    if isinstance(editable, Page) and not editable.template.is_block_shell:
        return tool_error("patch_content only supports the home page for classic templates.")
    editable = type(editable).objects.select_for_update().get(pk=editable.pk)
    stored = editable.content or {}

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

    catalog = blocks.catalog_for_template(tpl) if tpl.is_block_shell else None
    try:
        content_mod.read_field(schema, stored, field, catalog)
    except (KeyError, ValueError):
        return tool_error(f"Unknown field '{field}'.")

    if any(
        slot["id"] == field
        for slot in ghl_embed_slots.get_embed_slots(schema, stored, catalog=catalog)
    ):
        return tool_error(
            "GHL embed fields must be changed with set_embed_slot so tenant "
            "form ownership and published-page safeguards are enforced."
        )

    try:
        new_content = (
            content_mod.write_block_field(stored, catalog, field, value)
            if catalog and field.split(".", 1)[0] in content_mod.block_instances(stored, catalog)
            else content_mod.write_field(stored, field, value)
        )
        ghl_embed_slots.validate_embed_content_update(
            tenant=scope.tenant, schema=schema, current_content=stored,
            new_content=new_content, is_published=editable.is_published,
            catalog=catalog,
        )
    except (ValueError, ghl_embed_slots.GhlEmbedValidationError) as exc:
        return tool_error(str(exc))

    cv.save_editable_content(
        editable,
        new_content,
        user=auth.user,
        source=cv.SOURCE_MCP,
    )
    editable.refresh_from_db()
    new_etag = content_mod.content_etag(editable.content or {})
    url = tenant_canonical_public_url(
        scope.tenant, page_slug=editable.slug if isinstance(editable, Page) else None
    )
    return tool_success({"content_etag": new_etag, "url": url})


@transaction.atomic
def _apply_block_structure(auth: ResolvedAuth, *, site: str, page: Optional[str],
                           if_match: str, change) -> dict[str, Any]:
    scope = _require_scope(auth, site)
    if scope is None:
        return site_access_denied(site)
    try:
        editable, schema, _stored = content_mod.resolve_editable(scope.tenant, page)
    except LookupError:
        return page_access_denied(site, page or "")
    editable = type(editable).objects.select_for_update().get(pk=editable.pk)
    if not editable.template.is_block_shell:
        return tool_error("Structural block edits require a block-backed page.")
    if not editable.template.is_client_editable and not (
        getattr(auth.user, "is_superuser", False) or getattr(auth.user, "is_staff", False)
    ):
        return tool_error("This site isn't set up for editing yet — contact your agency.")
    stored = editable.content or {}
    if content_mod.content_etag(stored) != if_match:
        return tool_conflict("Conflict (409): content changed; re-read list_blocks and retry.")
    catalog = blocks.catalog_for_template(editable.template)
    try:
        proposed, instance_id = change(stored, catalog, editable.template.html_source)
        if proposed == stored:
            raise structure_mod.BlockStructureError("No structural change was requested.")
        ghl_embed_slots.validate_embed_content_update(
            tenant=scope.tenant, schema=_current_schema(editable, schema),
            current_content=stored, new_content=proposed,
            is_published=editable.is_published, catalog=catalog,
        )
    except (ValueError, ghl_embed_slots.GhlEmbedValidationError) as exc:
        return tool_error(str(exc))
    cv.save_editable_content(editable, proposed, user=auth.user, source=cv.SOURCE_MCP)
    editable.refresh_from_db()
    url = tenant_canonical_public_url(
        scope.tenant, page_slug=editable.slug if isinstance(editable, Page) else None
    )
    return tool_success({"content_etag": content_mod.content_etag(editable.content or {}),
                         "url": url, "instance_id": instance_id})


def add_block(auth: ResolvedAuth, *, site: str, block_type: str, region: str,
              position: int, if_match: str, parent_id: Optional[str] = None,
              page: Optional[str] = None) -> dict[str, Any]:
    def change(stored, catalog, shell):
        return structure_mod.add_block(stored, catalog, shell, block_type=block_type,
                                       parent_id=parent_id, region=region, position=position)
    return _apply_block_structure(auth, site=site, page=page, if_match=if_match, change=change)


def remove_block(auth: ResolvedAuth, *, site: str, instance_id: str,
                 if_match: str, page: Optional[str] = None) -> dict[str, Any]:
    def change(stored, catalog, shell):
        return structure_mod.remove_block(stored, catalog, shell, instance_id=instance_id), instance_id
    return _apply_block_structure(auth, site=site, page=page, if_match=if_match, change=change)


def move_block(auth: ResolvedAuth, *, site: str, instance_id: str, region: str,
               position: int, if_match: str, parent_id: Optional[str] = None,
               page: Optional[str] = None) -> dict[str, Any]:
    def change(stored, catalog, shell):
        return (structure_mod.move_block(stored, catalog, shell, instance_id=instance_id,
                                         parent_id=parent_id, region=region, position=position),
                instance_id)
    return _apply_block_structure(auth, site=site, page=page, if_match=if_match, change=change)


def append_block_richtext(auth: ResolvedAuth, *, site: str, field: str,
                          operation: str, text: str, if_match: str,
                          page: Optional[str] = None) -> dict[str, Any]:
    def change(stored, catalog, shell):
        return (structure_mod.append_block_richtext(
            stored, catalog, shell, field=field, operation=operation, text=text),
            field.split(".", 1)[0])
    return _apply_block_structure(auth, site=site, page=page, if_match=if_match, change=change)


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
    allow_field_drift: bool = False,
) -> tuple[Optional[dict], Optional[Any]]:
    """Apply if_match + save_template_version. Returns (error_result, SaveTemplateResult).

    ``if_match`` is compared inside the service's write transaction, while the
    template row is locked. Comparing it here first left a time-of-check /
    time-of-use gap in which another writer could commit between the check and
    the write.
    """
    try:
        result = save_template_version(
            template,
            html,
            user=user,
            allow_field_loss=bool(allow_field_loss),
            allow_field_drift=bool(allow_field_drift),
            label=label,
            expect_html_etag=if_match,
        )
    except ConcurrentWriteError as exc:
        return tool_conflict(str(exc)), None
    except (FieldLossError, FieldDriftError) as exc:
        return tool_error(str(exc)), None
    except ValueError as exc:
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
    allow_field_drift: bool = False,
    if_match: Optional[str] = None,
    template_id: Optional[int] = None,
) -> dict[str, Any]:
    """CMS-10: create or whole-HTML re-push a page (or the site home).

    Constraints (§9):
    - New templates are tenant-owned and ``editing_mode=raw``.
    - Re-push goes through ``save_template_version`` (appends TemplateVersion).
    - Field loss on published content requires ``allow_field_loss=true``.
    - Re-pointing a surviving field id at a different element requires
      ``allow_field_drift=true`` when a published site has edits stored on it.
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
        if explicit_tpl.is_block_shell:
            return tool_error("Block-backed templates cannot be changed with push_page.")

    if page_slug is not None and page_slug in RESERVED_PAGE_SLUGS:
        return tool_error(
            f"Slug '{page_slug}' is reserved by the CMS and cannot be used "
            f"as a page path."
        )

    # ---- Home (page omitted) ----
    if page_slug is None:
        if tenant.template.is_block_shell or (tenant.content or {}).get("regions"):
            return tool_error("Block-backed pages cannot be changed with push_page. Use get_block_html / push_block_html for copy, or the bounded block structure tools for add/remove/move.")
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
            allow_field_drift=allow_field_drift,
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
                "unchanged": result.unchanged,
            }
        )

    # ---- Inner page ----
    existing = (
        Page.objects.filter(tenant=tenant, slug=page_slug)
        .select_related("template")
        .first()
    )
    if existing is not None and (
        existing.template.is_block_shell or (existing.content or {}).get("regions")
    ):
        return tool_error("Block-backed pages cannot be changed with push_page. Use get_block_html / push_block_html for copy, or the bounded block structure tools for add/remove/move.")
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
                    allow_field_drift=allow_field_drift,
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
                    allow_field_drift=allow_field_drift,
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
                "unchanged": result.unchanged,
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
        allow_field_drift=allow_field_drift,
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
            "unchanged": result.unchanged,
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


def add_custom_domain(
    auth: ResolvedAuth,
    *,
    site: str,
    domain: str,
) -> dict[str, Any]:
    """CMS-32: register a custom domain for a site. Superuser only.

    Shares normalisation/validation with the dashboard's
    ``tenant_custom_domain_add`` via ``core.services.custom_domains`` so the
    two surfaces can't drift on what counts as a valid, available domain.
    """
    if not getattr(auth.user, "is_superuser", False):
        return _CUSTOM_DOMAIN_DENIED

    site = (site or "").strip().lower()
    if not site:
        return tool_error("site is required.")

    tenant = Tenant.objects.filter(subdomain=site).first()
    if tenant is None:
        return _CUSTOM_DOMAIN_DENIED

    custom_domain, error = custom_domains_svc.add_custom_domain(tenant, domain)
    if error:
        return tool_error(error)

    return tool_success(
        {
            "site": tenant.subdomain,
            "domain": custom_domain.domain,
            "is_verified": custom_domain.is_verified,
        }
    )


def verify_custom_domain(
    auth: ResolvedAuth,
    *,
    site: str,
    domain: str,
) -> dict[str, Any]:
    """CMS-32: check a custom domain's DNS and mark it verified if it matches.

    Shares the resolve-and-compare logic with the dashboard's
    ``tenant_custom_domain_verify`` via ``core.services.custom_domains``. On a
    non-match, ``resolved`` carries whatever the domain currently points at —
    the actionable detail for fixing DNS, not just "not verified".
    """
    if not getattr(auth.user, "is_superuser", False):
        return _CUSTOM_DOMAIN_DENIED

    site = (site or "").strip().lower()
    if not site:
        return tool_error("site is required.")

    tenant = Tenant.objects.filter(subdomain=site).first()
    if tenant is None:
        return _CUSTOM_DOMAIN_DENIED

    normalized = custom_domains_svc.normalize_domain(domain)
    custom_domain = CustomDomain.objects.filter(
        tenant=tenant, domain=normalized
    ).first()
    if custom_domain is None:
        return tool_error(
            f"No custom domain '{normalized}' registered for site '{site}'. "
            "Call add_custom_domain first."
        )

    verified, resolved = custom_domains_svc.verify_custom_domain(custom_domain)
    return tool_success(
        {
            "site": tenant.subdomain,
            "domain": custom_domain.domain,
            "is_verified": verified,
            "resolved": list(resolved),
            # Authorities that answered but not cleanly (CMS-65); any entry
            # blocks verification because Let's Encrypt may ask that one.
            "dns_problems": list(getattr(resolved, "problems", ())),
            "target_ip": settings.CUSTOM_DOMAIN_TARGET_IP,
        }
    )


# list_sites returns a plain dict; wrap at call site for MCP envelope.
HANDLERS: dict[str, Callable[..., Any]] = {
    "list_sites": list_sites,
    "get_connection_context": get_connection_context,
    "list_pages": list_pages,
    "list_blocks": list_blocks,
    "get_page": get_page,
    "get_page_html": get_page_html,
    "get_block_html": get_block_html,
    "push_block_html": push_block_html,
    "get_content": get_content,
    "list_embed_slots": list_embed_slots,
    "set_embed_slot": set_embed_slot,
    "list_ghl_forms": list_ghl_forms,
    "list_templates": list_templates,
    "create_client_account": create_client_account,
    "publish_site": publish_site,
    "publish_page": publish_page,
    "patch_content": patch_content,
    "add_block": add_block,
    "remove_block": remove_block,
    "move_block": move_block,
    "append_block_richtext": append_block_richtext,
    "push_page": push_page,
    "delete_page": delete_page,
    "add_custom_domain": add_custom_domain,
    "verify_custom_domain": verify_custom_domain,
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
                        "required": [
                            "site_id",
                            "subdomain",
                            "name",
                            "role",
                            "published",
                        ],
                        "additionalProperties": False,
                        "properties": {
                            "site_id": {"type": "string"},
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
        "name": "get_connection_context",
        "description": (
            "Return the stable current principal and site membership IDs used "
            "to bind an external tenant connection. Global superusers are "
            "not eligible."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "required": ["principal_id", "sites"],
            "additionalProperties": False,
            "properties": {
                "principal_id": {"type": "string"},
                "sites": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "site_id",
                            "subdomain",
                            "name",
                            "role",
                            "published",
                        ],
                        "additionalProperties": False,
                        "properties": {
                            "site_id": {"type": "string"},
                            "subdomain": {"type": "string"},
                            "name": {"type": "string"},
                            "role": {"type": "string"},
                            "published": {"type": "boolean"},
                        },
                    },
                },
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
        "name": "list_blocks",
        "description": (
            "List every stored block instance on a page in shell render order, including "
            "fieldless layouts and nested children. region is the top-level "
            "slot or a slot on parent_id; position is zero-based within it. "
            "available=false means the type is no longer allowlisted and cannot "
            "be edited. Use an ID with get_block_html or a field ID with get_content."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["site"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "page": {"type": ["string", "null"],
                         "description": "Page slug; omit/null for home"},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["page", "blocks", "content_etag"],
            "additionalProperties": False,
            "properties": {
                "page": {"type": ["string", "null"]},
                "blocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "type", "parent_id", "region",
                                     "position", "depth", "available"],
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string"},
                            "type": {"type": "string"},
                            "parent_id": {"type": ["string", "null"]},
                            "region": {"type": "string"},
                            "position": {"type": "integer"},
                            "depth": {"type": "integer"},
                            "available": {"type": "boolean"},
                        },
                    },
                },
                "content_etag": {"type": "string"},
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
            "get_page / get_content instead. On block-backed pages this is "
            "the shell only; use get_block_html for an instance."
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
        "name": "get_block_html",
        "description": "Read one rendered block instance, including nested children. The content_etag guards push_block_html, not push_page.",
        "inputSchema": {"type": "object", "required": ["site", "instance_id"],
            "additionalProperties": False, "properties": {
                "site": {"type": "string"}, "page": {"type": ["string", "null"]},
                "instance_id": {"type": "string"}}},
        "outputSchema": {"type": "object", "required": ["html", "instance_id", "content_etag"],
            "additionalProperties": False, "properties": {
                "html": {"type": "string"}, "instance_id": {"type": "string"},
                "content_etag": {"type": "string"}}},
        "annotations": ANNOTATIONS,
    },
    {
        "name": "push_block_html",
        "description": "Write text/richtext values in one rendered block fragment. Requires current content_etag; rejects changes to structure, attributes, nested blocks, and non-text fields. Use bounded block structure tools or the visual editor for structural changes.",
        "inputSchema": {"type": "object", "required": ["site", "instance_id", "html", "if_match"],
            "additionalProperties": False, "properties": {
                "site": {"type": "string"}, "page": {"type": ["string", "null"]},
                "instance_id": {"type": "string"}, "html": {"type": "string"},
                "if_match": {"type": "string"}}},
        "outputSchema": {"type": "object", "required": ["instance_id", "content_etag"],
            "additionalProperties": False, "properties": {
                "instance_id": {"type": "string"}, "content_etag": {"type": "string"}}},
        "annotations": WRITE_ANNOTATIONS,
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
        "name": "list_embed_slots",
        "description": (
            "List GHL embed slots and current kind-prefixed values for a site page."
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
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["site", "page", "published", "slots", "content_etag"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "page": {"type": ["string", "null"]},
                "published": {"type": "boolean"},
                "slots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "label", "kind", "value"],
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "kind": {"type": "string", "enum": ["form"]},
                            "value": {
                                "type": "string",
                                "description": "Empty or form:<id>",
                            },
                        },
                    },
                },
                "content_etag": {"type": "string"},
            },
        },
        "annotations": ANNOTATIONS,
    },
    {
        "name": "list_ghl_forms",
        "description": (
            "List forms from the site's bound GoHighLevel location. "
            "Values are ready to pass to set_embed_slot."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["site"],
            "additionalProperties": False,
            "properties": {"site": {"type": "string"}},
        },
        "outputSchema": {
            "type": "object",
            "required": ["site", "forms"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "forms": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "name", "value"],
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "value": {
                                "type": "string",
                                "description": "form:<id>",
                            },
                        },
                    },
                },
            },
        },
        "annotations": ANNOTATIONS,
    },
    {
        "name": "set_embed_slot",
        "description": (
            "Set an existing GHL form embed slot. Requires form:<id> from "
            "list_ghl_forms and a current content_etag from list_embed_slots. "
            "A populated slot cannot be cleared while its page is published."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["site", "field", "value", "if_match"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "page": {
                    "type": ["string", "null"],
                    "description": "Page slug; omit/null for home",
                },
                "field": {"type": "string"},
                "value": {
                    "type": "string",
                    "description": "form:<id>, or empty to unset an unpublished page",
                },
                "if_match": {"type": "string"},
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["site", "page", "field", "value", "content_etag"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "page": {"type": ["string", "null"]},
                "field": {"type": "string"},
                "value": {"type": "string"},
                "content_etag": {"type": "string"},
            },
        },
        "annotations": WRITE_ANNOTATIONS,
    },
    {
        "name": "list_templates",
        "description": (
            "List agency-library templates (tenant IS NULL) — the set "
            "create_client_account will accept. Superuser only. Each row's "
            "'id' is the value to pass as create_client_account's "
            "template_id, so the two calls can be chained directly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "required": ["templates"],
            "additionalProperties": False,
            "properties": {
                "templates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "id",
                            "name",
                            "slug",
                            "description",
                            "editing_mode",
                        ],
                        "additionalProperties": False,
                        "properties": {
                            "id": {
                                "type": "integer",
                                "description": (
                                    "Pass as create_client_account's "
                                    "template_id"
                                ),
                            },
                            "name": {"type": "string"},
                            "slug": {"type": "string"},
                            "description": {"type": "string"},
                            "editing_mode": {"type": "string"},
                        },
                    },
                }
            },
        },
        "annotations": ANNOTATIONS,
    },
    {
        "name": "create_client_account",
        "description": (
            "Create a new client site and owner login. Superuser only. "
            "Supply exactly one of template_id (clone a library template) or "
            "html (create the home page from that HTML). HTML produces a "
            "raw, non-field-editable site until annotated. Sites are created "
            "unpublished. The generated password is returned once in the tool "
            "result and is never stored in plaintext."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["name", "subdomain", "username", "email"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "description": "Site display name"},
                "subdomain": {"type": "string", "description": "Tenant subdomain"},
                "username": {"type": "string", "description": "Owner login username"},
                "email": {"type": "string", "description": "Owner email"},
                "template_id": {
                    "type": "integer",
                    "description": (
                        "Library template primary key to clone. Mutually "
                        "exclusive with html."
                    ),
                },
                "html": {
                    "type": "string",
                    "description": (
                        "Raw HTML for the site home. Creates a tenant-owned "
                        "template (raw / non-field-editable unless it already "
                        "has data-edit annotations). Mutually exclusive with "
                        "template_id."
                    ),
                },
                "custom_domain": {
                    "type": "string",
                    "description": (
                        "Optional custom domain hostname (e.g. www.acme.com). "
                        "Attached to the new site immediately but starts "
                        "UNVERIFIED — nothing serves on it yet. Point an A "
                        "record at settings.CUSTOM_DOMAIN_TARGET_IP, DNS-only "
                        "(a Cloudflare-proxied/orange-cloud record resolves to "
                        "Cloudflare's IPs and can never verify), then call "
                        "verify_custom_domain. An invalid or already-registered "
                        "domain fails the whole account creation — no user or "
                        "site is left behind."
                    ),
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
            "Write one field by dotted id. Block instances use "
            "<instance-id>.<field-name>; repeated types keep distinct IDs. "
            "Requires current content_etag from get_content/get_page. "
            "Block-backed inner pages are supported."
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
                    "description": "Block-backed inner page slug; omit/null for home",
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
            "to push the site home. Existing block-backed pages reject this "
            "tool before mutation; use get_block_html / push_block_html for copy "
            "and bounded block structure tools for add/remove/move. "
            "New pages get a tenant-owned template in "
            "editing_mode=raw. Re-pushes append a TemplateVersion. Use if_match "
            "(html etag from get_page_html or a prior push) to guard concurrent "
            "writes; use "
            "allow_field_loss=true when a re-push drops fields a published page "
            "still holds. Reserved slugs (api, mcp, dashboard, …) are refused; "
            "privacy/terms are allowed (platform legal pages are host-scoped). "
            "html is stored byte-for-byte; a \\uXXXX escape inside it is decoded "
            "by JSON before this tool runs, so to keep a literal backslash-u "
            "(e.g. in inline JS) double the backslash on the wire, or use the "
            "character itself."
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
                "allow_field_drift": {
                    "type": "boolean",
                    "description": (
                        "Confirm re-pointing surviving field ids at different "
                        "elements (renumbered generated ids), which moves edited "
                        "copy a published site holds"
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
    {
        "name": "add_custom_domain",
        "description": (
            "Register a custom domain for a site. Superuser only. Normalises "
            "the hostname (lowercase, trailing dot stripped) and validates its "
            "format; refuses a domain already registered to any tenant. The "
            "domain starts unverified — call verify_custom_domain once its "
            "A record is pointed at this platform's origin IP."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["site", "domain"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string", "description": "Tenant subdomain"},
                "domain": {
                    "type": "string",
                    "description": "Hostname to register, e.g. www.acme.com",
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["site", "domain", "is_verified"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "domain": {"type": "string"},
                "is_verified": {"type": "boolean"},
            },
        },
        "annotations": WRITE_ANNOTATIONS,
    },
    {
        "name": "verify_custom_domain",
        "description": (
            "Check whether a custom domain's A record resolves to this "
            "platform's origin, and mark it verified if so. Superuser only. "
            "IMPORTANT: the A record must be DNS-only. If the domain is "
            "proxied (e.g. Cloudflare's orange cloud), resolution returns the "
            "proxy's IPs instead of ours and verification can NEVER succeed no "
            "matter how long you wait — that looks identical to normal DNS "
            "propagation delay but isn't one; tell the user to disable the "
            "proxy/orange-cloud for this record. On a non-match this returns "
            "'resolved' — what the domain currently points at — which is the "
            "actionable detail for fixing DNS, not just 'not verified'."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["site", "domain"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string", "description": "Tenant subdomain"},
                "domain": {
                    "type": "string",
                    "description": "Previously registered hostname to check",
                },
            },
        },
        "outputSchema": {
            "type": "object",
            "required": ["site", "domain", "is_verified", "resolved", "target_ip"],
            "additionalProperties": False,
            "properties": {
                "site": {"type": "string"},
                "domain": {"type": "string"},
                "is_verified": {"type": "boolean"},
                "resolved": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "IPv4 addresses the domain currently resolves to",
                },
                "target_ip": {
                    "type": "string",
                    "description": "The IP the domain must resolve to for verification",
                },
            },
        },
        "annotations": WRITE_ANNOTATIONS,
    },
]


_STRUCTURE_RESULT = {
    "type": "object", "required": ["content_etag", "url", "instance_id"],
    "additionalProperties": False,
    "properties": {"content_etag": {"type": "string"}, "url": {"type": "string"},
                   "instance_id": {"type": "string"}},
}
_STRUCTURE_COMMON = {
    "site": {"type": "string", "description": "Tenant subdomain"},
    "page": {"type": ["string", "null"], "description": "Omit/null for home"},
    "if_match": {"type": "string", "description": "Current list_blocks content_etag"},
}
_STRUCTURE_DESTINATION = {
    "parent_id": {"type": ["string", "null"],
                  "description": "Omit/null for a body shell region; otherwise a layout block ID"},
    "region": {"type": "string", "description": "Shell body region or declared child slot"},
    "position": {"type": "integer", "description": "Zero-based insertion index; for moves, after removal"},
}
TOOLS_LIST.extend([
    {
        "name": "add_block",
        "description": "Insert one allowlisted block in a body or declared layout region. Generates a fresh stable ID; enforces the 40-block and depth-2 caps. No raw HTML input.",
        "inputSchema": {"type": "object", "required": ["site", "block_type", "region", "position", "if_match"],
                        "additionalProperties": False,
                        "properties": {**_STRUCTURE_COMMON, **_STRUCTURE_DESTINATION,
                                       "block_type": {"type": "string"}}},
        "outputSchema": _STRUCTURE_RESULT, "annotations": WRITE_ANNOTATIONS,
    },
    {
        "name": "remove_block",
        "description": "Remove one available block and its nested children. Published GHL forms cannot be removed. The previous content is versioned for undo.",
        "inputSchema": {"type": "object", "required": ["site", "instance_id", "if_match"],
                        "additionalProperties": False,
                        "properties": {**_STRUCTURE_COMMON, "instance_id": {"type": "string"}}},
        "outputSchema": _STRUCTURE_RESULT, "annotations": WRITE_ANNOTATIONS,
    },
    {
        "name": "move_block",
        "description": "Move one available block, preserving its ID and children. Position is zero-based in the destination after source removal. Descendant and over-depth moves are rejected.",
        "inputSchema": {"type": "object", "required": ["site", "instance_id", "region", "position", "if_match"],
                        "additionalProperties": False,
                        "properties": {**_STRUCTURE_COMMON, **_STRUCTURE_DESTINATION,
                                       "instance_id": {"type": "string"}}},
        "outputSchema": _STRUCTURE_RESULT, "annotations": WRITE_ANNOTATIONS,
    },
    {
        "name": "append_block_richtext",
        "description": "Append a plain-text paragraph or unordered list item to an available richtext block field. Accepts no HTML; text is escaped. Existing content and non-richtext fields are protected.",
        "inputSchema": {"type": "object", "required": ["site", "field", "operation", "text", "if_match"],
                        "additionalProperties": False,
                        "properties": {**_STRUCTURE_COMMON, "field": {"type": "string"},
                                       "operation": {"type": "string", "enum": ["paragraph", "list_item"]},
                                       "text": {"type": "string"}}},
        "outputSchema": _STRUCTURE_RESULT, "annotations": WRITE_ANNOTATIONS,
    },
])


TOOL_REQUIRED_OAUTH_SCOPES: dict[str, str] = {
    tool["name"]: (
        "read" if tool["annotations"]["readOnlyHint"] else "write"
    )
    for tool in TOOLS_LIST
}


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


# S03/S05 keep public publish/domain service operations inactive until S10 adds
# approval-aware endpoints. Runtime credentials can read and edit drafts only.
_SERVICE_DRAFT_TOOLS = {
    "patch_content", "push_page", "set_embed_slot", "push_block_html",
    "add_block", "remove_block", "move_block", "append_block_richtext",
}
_SERVICE_READ_EXCLUDED = {"get_connection_context", "list_templates"}


def _service_draft_allowed(auth: ServicePrincipal, name: str, args: dict) -> bool:
    """A draft grant cannot alter a published resource or a shared template."""
    if args.get("site") != auth.tenant.subdomain:
        return False
    tenant = auth.tenant
    page_slug = args.get("page")
    if page_slug:
        if not isinstance(page_slug, str):
            return False
        page = Page.objects.filter(tenant=tenant, slug=page_slug.strip().lower()).first()
        if name == "push_page" and args.get("template_id") is not None:
            return False
        if page is not None and page.is_published:
            return False
        if name == "push_page" and page is not None:
            template_id = page.template_id
        else:
            return True
    else:
        if tenant.is_published:
            return False
        template_id = tenant.template_id
    if name != "push_page":
        return True
    return not (
        Page.objects.filter(template_id=template_id, is_published=True).exists()
        or Tenant.objects.filter(template_id=template_id, is_published=True).exists()
    )


def _run_service_draft(auth: ServicePrincipal, name: str, args: dict, handler):
    """Serialize a draft write with publication of its target and shared HTML."""
    with transaction.atomic():
        tenant = Tenant.objects.select_for_update().get(pk=auth.tenant.pk)
        locked_auth = replace(auth, tenant=tenant)
        page_slug = args.get("page")
        page = None
        if page_slug:
            page = (
                Page.objects.select_for_update()
                .filter(tenant=tenant, slug=page_slug)
                .first()
            )
        if name == "push_page":
            template_id = page.template_id if page else tenant.template_id if not page_slug else None
            if template_id is not None:
                # Publishing any referrer to this template must wait for the
                # draft transaction, then decide against the committed HTML.
                list(Page.objects.select_for_update().filter(template_id=template_id).order_by("pk"))
                list(Tenant.objects.select_for_update().filter(template_id=template_id).order_by("pk"))
        if not _service_draft_allowed(locked_auth, name, args):
            return _CREATE_DENIED, None
        return handler(locked_auth, **args), None


def call_tool(
    auth: ResolvedAuth, name: str, arguments: Optional[dict]
) -> tuple[Optional[dict], Optional[dict]]:
    """Return (result, rpc_error). Exactly one is set."""
    if name not in HANDLERS:
        return None, {"code": METHOD_NOT_FOUND, "message": f"Unknown tool '{name}'"}
    required_scope = TOOL_REQUIRED_OAUTH_SCOPES[name]
    service = isinstance(auth, ServicePrincipal)
    if service:
        if name in _SERVICE_DRAFT_TOOLS:
            allowed = "draft" in auth.scopes
        else:
            allowed = (
                required_scope == "read"
                and name not in _SERVICE_READ_EXCLUDED
                and "read" in auth.scopes
            )
        if not allowed:
            return tool_error("Service credential is not permitted for this tool."), None
    elif not auth.has_oauth_scope(required_scope):
        return tool_error(
            f"OAuth scope '{required_scope}' is required for this tool."
        ), None
    args = dict(arguments or {})
    err = _validate_arguments(name, args)
    if err:
        return None, {"code": INVALID_PARAMS, "message": err}
    if service and name in _SERVICE_DRAFT_TOOLS and isinstance(args.get("page"), str):
        args["page"] = args["page"].strip().lower() or None

    if name == "list_sites":
        return tool_success(list_sites(auth)), None

    # Privileged tools: deny non-superusers before any lookup that could leak.
    if name in (
        "create_client_account",
        "publish_site",
        "publish_page",
        "delete_page",
        "list_templates",
        "add_custom_domain",
        "verify_custom_domain",
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
    if "html" in args and args["html"] is None:
        args.pop("html")
    if "allow_field_loss" in args and args["allow_field_loss"] is None:
        args.pop("allow_field_loss")
    if "allow_field_drift" in args and args["allow_field_drift"] is None:
        args.pop("allow_field_drift")
    if "version" in args and args["version"] is None:
        args.pop("version")
    if "force" in args and args["force"] is None:
        args.pop("force")
    if service and name in _SERVICE_DRAFT_TOOLS:
        return _run_service_draft(auth, name, args, handler)
    return handler(auth, **args), None
