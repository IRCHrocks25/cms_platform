"""API-surface persistence. MCP audit lives here (CMS-6 pulled into CMS-9)."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class McpAuditLog(models.Model):
    """One row per MCP ``tools/call`` — including read-only tools.

    The team initially shares a superuser Claude connection, so read
    attribution (who looked at which client's content) is the primary
    signal. Write tools (CMS-7/10/11) will reuse the same table later.
    """

    VIA_MCP = "MCP"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mcp_audit_logs",
    )
    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mcp_audit_logs",
    )
    action = models.CharField(max_length=80)
    performed_via = models.CharField(max_length=32, default=VIA_MCP)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["tenant", "-timestamp"]),
            models.Index(fields=["actor", "-timestamp"]),
        ]

    def __str__(self) -> str:
        who = getattr(self.actor, "username", "?")
        site = getattr(self.tenant, "subdomain", "-")
        return f"{self.timestamp:%Y-%m-%d %H:%M} {who} {self.action} @{site}"
