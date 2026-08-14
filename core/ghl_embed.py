"""Contracts shared by GHL embed annotations, rendering, and write paths."""

from __future__ import annotations

import re


VALID_GHL_EMBED_KINDS = frozenset({"form"})
_EMBED_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def parse_ghl_embed_value(
    value: object,
    *,
    expected_kind: str = "form",
) -> tuple[str, str] | None:
    """Return ``(kind, id)`` for an approved ``kind:<id>`` value.

    Empty values represent an intentionally unset slot. Everything else must
    stay self-describing and contain an opaque identifier, never a URL/path.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    prefix = f"{expected_kind}:"
    if not raw.startswith(prefix):
        raise ValueError(f"GHL {expected_kind} embed value must be {expected_kind}:<id>.")
    embed_id = raw[len(prefix) :]
    if not _EMBED_ID_RE.fullmatch(embed_id):
        raise ValueError(f"GHL {expected_kind} embed value must be {expected_kind}:<id>.")
    return expected_kind, embed_id
