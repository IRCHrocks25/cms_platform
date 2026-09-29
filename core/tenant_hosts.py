"""Tenant host-label helpers shared by URL generation and request routing."""

from django.conf import settings


def tenant_subdomain_suffix() -> str:
    """Optional environment suffix appended inside the single DNS label."""
    return (getattr(settings, "TENANT_SUBDOMAIN_SUFFIX", "") or "").strip().lower()


def tenant_host_label(subdomain: str) -> str:
    """Return the configured one-label hostname component for a tenant."""
    return f"{subdomain}{tenant_subdomain_suffix()}"


def tenant_subdomain_from_host_label(
    label: str,
    *,
    use_suffix: bool = True,
) -> str | None:
    """Reverse ``tenant_host_label`` or reject a label outside this environment."""
    suffix = tenant_subdomain_suffix() if use_suffix else ""
    if not suffix:
        return label
    if not label.endswith(suffix):
        return None
    subdomain = label[: -len(suffix)]
    return subdomain or None
