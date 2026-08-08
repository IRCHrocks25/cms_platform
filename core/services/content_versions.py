"""Shared snapshot-then-save for tenant home content.

Dashboard editor saves and MCP ``patch_content`` both call this so AI writes
cannot bypass undo. MCP and dashboard versions are retained separately so a
burst of per-field AI patches cannot flush the client's human undo history.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from core.models import ContentVersion, Tenant


SOURCE_DASHBOARD = "dashboard"
SOURCE_MCP = "mcp"

SOURCE_CHOICES = (
    (SOURCE_DASHBOARD, "Dashboard"),
    (SOURCE_MCP, "MCP"),
)

DASHBOARD_KEEP = 10
MCP_KEEP = 10
MCP_COALESCE_WINDOW = timedelta(minutes=15)


def _trim_versions(tenant: Tenant, *, source: str, keep: int) -> None:
    keep_ids = list(
        tenant.versions.filter(source=source)
        .order_by("-saved_at")
        .values_list("id", flat=True)[:keep]
    )
    tenant.versions.filter(source=source).exclude(id__in=keep_ids).delete()


def _should_coalesce_mcp(tenant: Tenant, user) -> bool:
    latest = (
        tenant.versions.filter(source=SOURCE_MCP).order_by("-saved_at").first()
    )
    if latest is None:
        return False
    user_id = getattr(user, "pk", None)
    if latest.saved_by_id != user_id:
        return False
    age = timezone.now() - latest.saved_at
    return age <= MCP_COALESCE_WINDOW


def save_tenant_content(
    tenant: Tenant,
    content: dict[str, Any],
    *,
    user,
    source: str = SOURCE_DASHBOARD,
) -> Tenant:
    """Snapshot previous home content (when appropriate), write, trim.

    ``source=mcp`` coalesces rapid saves by the same user into one snapshot
    per burst, and trims MCP rows independently of dashboard rows.
    """
    if source not in {SOURCE_DASHBOARD, SOURCE_MCP}:
        raise ValueError(f"unknown content version source: {source!r}")

    with transaction.atomic():
        snapshot_now = True
        if source == SOURCE_MCP and _should_coalesce_mcp(tenant, user):
            snapshot_now = False

        if snapshot_now:
            ContentVersion.objects.create(
                tenant=tenant,
                snapshot=deepcopy(tenant.content or {}),
                saved_by=user if getattr(user, "pk", None) else None,
                source=source,
            )

        tenant.content = content
        tenant.save(update_fields=["content", "updated_at"])

        _trim_versions(tenant, source=SOURCE_DASHBOARD, keep=DASHBOARD_KEEP)
        _trim_versions(tenant, source=SOURCE_MCP, keep=MCP_KEEP)

    return tenant


def restore_tenant_content(
    tenant: Tenant,
    version: ContentVersion,
    *,
    user,
) -> Tenant:
    """Restore a snapshot; snapshot current first so restore is undoable."""
    if version.tenant_id != tenant.pk:
        raise ValueError("version does not belong to tenant")

    with transaction.atomic():
        ContentVersion.objects.create(
            tenant=tenant,
            snapshot=deepcopy(tenant.content or {}),
            saved_by=user if getattr(user, "pk", None) else None,
            source=SOURCE_DASHBOARD,
        )
        tenant.content = deepcopy(version.snapshot or {})
        tenant.save(update_fields=["content", "updated_at"])
        _trim_versions(tenant, source=SOURCE_DASHBOARD, keep=DASHBOARD_KEEP)
        _trim_versions(tenant, source=SOURCE_MCP, keep=MCP_KEEP)

    return tenant
