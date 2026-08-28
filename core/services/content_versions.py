"""Shared snapshot-then-save for editable content (tenant home + inner pages).

Dashboard editor saves and MCP ``patch_content`` both call this so AI writes
cannot bypass undo. MCP and dashboard versions are retained separately so a
burst of per-field AI patches cannot flush the client's human undo history.

Undo now covers inner ``Page`` content as well as the tenant home. A
``ContentVersion`` row belongs to the home when ``page`` is NULL and to a
specific page otherwise; the two are trimmed as independent buckets so a page
edit can never evict a home snapshot (or vice versa). The rolling count was
raised from 10 to 25 because block add / remove / reorder are exactly the ops a
client will fat-finger, so a deeper structural undo is worth the rows.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import ContentVersion, Page, Tenant
from core.renderer import drop_blank_instance_fields, strip_defaults


SOURCE_DASHBOARD = "dashboard"
SOURCE_MCP = "mcp"


class RestoreValidationError(Exception):
    """A snapshot can't be restored as-is (structurally stale: over cap, a block
    type no longer allowed, or children nested past the depth cap)."""

SOURCE_CHOICES = (
    (SOURCE_DASHBOARD, "Dashboard"),
    (SOURCE_MCP, "MCP"),
)

# Raised 10 -> 25: structural block ops (add/remove/reorder/duplicate) make a
# usable undo depth matter more than it did for pure field edits.
DASHBOARD_KEEP = 25
MCP_KEEP = 10
MCP_COALESCE_WINDOW = timedelta(minutes=15)


def _page_filter(page: Page | None) -> Q:
    return Q(page__isnull=True) if page is None else Q(page=page)


def _trim_versions(tenant: Tenant, page: Page | None, *, source: str, keep: int) -> None:
    bucket = tenant.versions.filter(_page_filter(page), source=source)
    keep_ids = list(
        bucket.order_by("-saved_at").values_list("id", flat=True)[:keep]
    )
    bucket.exclude(id__in=keep_ids).delete()


def _should_coalesce_mcp(tenant: Tenant, page: Page | None, user) -> bool:
    latest = (
        tenant.versions.filter(_page_filter(page), source=SOURCE_MCP)
        .order_by("-saved_at")
        .first()
    )
    if latest is None:
        return False
    user_id = getattr(user, "pk", None)
    if latest.saved_by_id != user_id:
        return False
    age = timezone.now() - latest.saved_at
    return age <= MCP_COALESCE_WINDOW


def _resolve(editable) -> tuple[Tenant, Page | None]:
    """Return (tenant, page) for a Tenant home or an inner Page."""
    if isinstance(editable, Tenant):
        return editable, None
    if isinstance(editable, Page):
        return editable.tenant, editable
    raise TypeError(f"not a versioned editable: {editable!r}")


def save_editable_content(
    editable,
    content: dict[str, Any],
    *,
    user,
    source: str = SOURCE_DASHBOARD,
):
    """Snapshot previous content (when appropriate), write, trim.

    Works for both the tenant home (``Tenant``) and an inner ``Page``.
    ``source=mcp`` coalesces rapid saves by the same user into one snapshot per
    burst, and trims MCP rows independently of dashboard rows.
    """
    if source not in {SOURCE_DASHBOARD, SOURCE_MCP}:
        raise ValueError(f"unknown content version source: {source!r}")

    tenant, page = _resolve(editable)

    # Persist authored overrides only. The editor posts back every field it was
    # given, and merge_with_defaults gave it all of them. Meta keys (_header,
    # _styles, regions, …) pass through strip_defaults untouched.
    schema = getattr(editable.template, "schema", None) if getattr(editable, "template_id", None) else None
    if isinstance(schema, dict):
        content = strip_defaults(schema, content)
    # Contenteditable leftovers (`<br>`, empty strings) must not persist as
    # overrides — they wipe designed body copy on the next preview apply.
    drop_blank_instance_fields(content)

    with transaction.atomic():
        snapshot_now = True
        if source == SOURCE_MCP and _should_coalesce_mcp(tenant, page, user):
            snapshot_now = False

        if snapshot_now:
            ContentVersion.objects.create(
                tenant=tenant,
                page=page,
                snapshot=deepcopy(editable.content or {}),
                saved_by=user if getattr(user, "pk", None) else None,
                source=source,
            )

        editable.content = content
        editable.save(update_fields=["content", "updated_at"])

        _trim_versions(tenant, page, source=SOURCE_DASHBOARD, keep=DASHBOARD_KEEP)
        _trim_versions(tenant, page, source=SOURCE_MCP, keep=MCP_KEEP)

    return editable


def save_tenant_content(
    tenant: Tenant,
    content: dict[str, Any],
    *,
    user,
    source: str = SOURCE_DASHBOARD,
) -> Tenant:
    """Home-content wrapper kept for MCP + existing callers."""
    return save_editable_content(tenant, content, user=user, source=source)


def _validate_embeds(editable, proposed: dict[str, Any]) -> None:
    # A historical snapshot can contain a form that was deleted or belongs to
    # another location. Re-run the same tenant-scoped validation used by live
    # writes before mutating content.
    from core.parser import build_schema
    from core.services import ghl_embed_slots

    tenant, _page = _resolve(editable)
    schema = build_schema(editable.template.html_source)
    ghl_embed_slots.validate_embed_content_update(
        tenant=tenant,
        schema=schema,
        current_content=editable.content or {},
        new_content=proposed,
        is_published=editable.is_published,
    )


def restore_editable_content(
    editable, version: ContentVersion, *, user, pop: bool = False
):
    """Restore a snapshot.

    Two intents share this path:

    * **Arbitrary restore** (``pop=False``, the version-history UI): snapshot the
      current content first so the restore is itself undoable (a redo point).
    * **Linear undo** (``pop=True``, the editor's Undo button / Ctrl+Z): step one
      state back. We must NOT push a redo point here — doing so makes that fresh
      snapshot the newest version, so a second undo would restore it and bounce
      the user forward instead of walking further back. Instead we consume the
      restored snapshot so repeated undo steps monotonically back through history.
    """
    tenant, page = _resolve(editable)
    if version.tenant_id != tenant.pk or version.page_id != (page.pk if page else None):
        raise ValueError("version does not belong to this page")

    proposed = deepcopy(version.snapshot or {})
    # Snapshots taken before sparse content are full copies of the defaults.
    # Restoring one verbatim put every default back, and if the template had
    # moved on since, re-created the displacement a prune had just cleared.
    html = getattr(getattr(editable, "template", None), "html_source", "") or ""
    if html:
        from core.parser import build_schema

        proposed = strip_defaults(build_schema(html), proposed)
    _validate_embeds(editable, proposed)

    # A historical snapshot can be structurally stale — more blocks than the
    # current per-page cap, a block type since removed from the template's
    # allowlist, children nested past the depth cap, or ids that don't match the
    # current shape. Re-run the same normalization live saves use so a restore
    # can't reintroduce an invalid tree; reject rather than silently write it
    # (E13). Imported lazily to avoid a core<->dashboard import cycle.
    from dashboard.views import _BlockValidationError, _normalize_regions

    try:
        _normalize_regions(proposed, editable.template)
    except _BlockValidationError as exc:
        raise RestoreValidationError(str(exc)) from exc

    with transaction.atomic():
        if not pop:
            ContentVersion.objects.create(
                tenant=tenant,
                page=page,
                snapshot=deepcopy(editable.content or {}),
                saved_by=user if getattr(user, "pk", None) else None,
                source=SOURCE_DASHBOARD,
            )
        editable.content = proposed
        editable.save(update_fields=["content", "updated_at"])
        if pop:
            ContentVersion.objects.filter(pk=version.pk).delete()
        _trim_versions(tenant, page, source=SOURCE_DASHBOARD, keep=DASHBOARD_KEEP)
        _trim_versions(tenant, page, source=SOURCE_MCP, keep=MCP_KEEP)

    return editable


def restore_tenant_content(
    tenant: Tenant, version: ContentVersion, *, user, pop: bool = False
) -> Tenant:
    """Home-content restore wrapper kept for existing callers."""
    return restore_editable_content(tenant, version, user=user, pop=pop)
