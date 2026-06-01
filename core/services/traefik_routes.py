"""Generate the Traefik dynamic file that routes verified custom client domains
to this app.

Why a generated file instead of a `HostRegexp(`.+`)` catch-all: the Dokploy host
is SHARED with other stacks. A catch-all would make this app the default backend
for every otherwise-unmatched host on the box. Instead we emit one router per
verified ``CustomDomain``, so we only ever claim domains we own.

The file is regenerated wholesale from the DB (no incremental append/remove
drift) and written atomically (temp file + ``os.replace``). Traefik's file
provider (``directory: /etc/dokploy/traefik/dynamic``, ``watch: true``,
recursive) hot-reloads it. Writing is gated on ``settings.TRAEFIK_DYNAMIC_DIR``;
when unset (dev/test, and the web container — which deliberately has no Traefik
mount) every call is a no-op. Only the isolated ``route-syncer`` service sets the
dir and actually writes (see deploy/DOKPLOY.md).

Routers use ``HostRegexp(`^<domain>$`)``, NOT ``Host(`<domain>`)``: the websecure
entrypoint defaults ``certResolver=letsencrypt``, and a ``Host()`` router would
inherit it and try to ACME-issue for the client domain. ``HostRegexp`` exposes no
extractable domain, so Traefik serves the default-store cert (the Cloudflare
Origin CA) — correct under Cloudflare SSL=Full, where CF terminates the client's
public TLS at the edge. Same reasoning as the apex router in docker-compose.yml.
``service: cms-web@docker`` is the Compose-label service (docker provider).
"""
import json
import logging
import os
import re
import tempfile

from django.conf import settings

logger = logging.getLogger(__name__)

# Must end in .yml/.yaml/.toml: Traefik's file provider selects its parser by
# extension and silently ignores .json files, so the router would never load.
# We still emit JSON *content* below — JSON is a strict subset of YAML, so the
# YAML parser reads it fine — which avoids taking on a PyYAML dependency.
ROUTES_FILENAME = "custom-domains.yml"

# Hosts a CustomDomain row must NEVER emit a router for — our own infrastructure.
# With the `.+` catch-all gone, a stray or hostile verified row is the only way a
# wrong router could ever appear, so we hard-skip these as a safety net. The
# tenant base domain and all of its subdomains are also protected dynamically
# (they belong to the apex / tenant-wildcard routers in docker-compose.yml).
PROTECTED_HOSTS = frozenset(
    {
        "katek.app",
        "sites.katek.app",
        "proxy.sites.katek.app",
        "dokploy.katek.app",
    }
)


def _dynamic_dir():
    return (getattr(settings, "TRAEFIK_DYNAMIC_DIR", "") or "").strip() or None


def _is_protected(domain: str) -> bool:
    base = (getattr(settings, "TENANT_BASE_DOMAIN", "") or "").lower().strip(".")
    if domain in PROTECTED_HOSTS:
        return True
    if base and (domain == base or domain.endswith("." + base)):
        return True
    return False


def _build_config(domains):
    routers = {}
    for cd in domains:
        if _is_protected(cd.domain):
            logger.warning(
                "Refusing to route protected host %r (CustomDomain pk=%s); skipped.",
                cd.domain,
                cd.pk,
            )
            continue
        routers[f"cms-cd-{cd.pk}"] = {
            "rule": f"HostRegexp(`^{re.escape(cd.domain)}$`)",
            "entryPoints": ["websecure"],
            "service": "cms-web@docker",
            "tls": {},
        }
    return {"http": {"routers": routers}}  # routers: {} when empty — valid config


def sync_custom_domain_routes() -> bool:
    """Regenerate the custom-domain dynamic file from verified CustomDomains.

    Returns True if the file is up to date afterwards (whether or not a write was
    needed), False if writing is disabled/unavailable or the write failed. Never
    raises — callers treat it as best-effort.
    """
    from core.models import CustomDomain  # local import: avoid app-load cycle

    target_dir = _dynamic_dir()
    if not target_dir or not os.path.isdir(target_dir):
        logger.info(
            "TRAEFIK_DYNAMIC_DIR unset or missing (%r); skipping route sync.",
            target_dir,
        )
        return False

    domains = list(CustomDomain.objects.filter(is_verified=True).order_by("domain"))
    payload = json.dumps(_build_config(domains), indent=2) + "\n"
    target = os.path.join(target_dir, ROUTES_FILENAME)

    # Change-detection: the syncer polls on a loop, so skip the write (and the
    # Traefik reload it would trigger) when nothing changed.
    try:
        with open(target, "r", encoding="utf-8") as fh:
            if fh.read() == payload:
                return True
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("Could not read existing %s; will attempt rewrite", target)

    fd, tmp = tempfile.mkstemp(dir=target_dir, prefix=".custom-domains.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, target)  # atomic on the same filesystem
    except Exception:
        logger.exception("Failed writing Traefik custom-domain routes to %s", target)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False

    logger.info("Synced %d custom-domain route(s) to %s", len(domains), target)
    return True
