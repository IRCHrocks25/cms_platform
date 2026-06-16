"""Generate the Traefik dynamic file that routes verified custom client domains
to this app.

Why a generated file instead of a `HostRegexp(`.+`)` catch-all: the Dokploy host
is SHARED with other stacks. A catch-all would make this app the default backend
for every otherwise-unmatched host on the box. Instead we emit one router per
verified ``CustomDomain``, so we only ever claim domains we own.

The file is regenerated wholesale from the DB (no incremental append/remove
drift) and written atomically (temp file + ``os.replace``). Traefik's file
provider (``directory: /etc/dokploy/traefik/dynamic``, ``watch: true``)
hot-reloads it. Writing is gated on ``settings.TRAEFIK_DYNAMIC_DIR``; when unset
(dev/test, and the web container — which deliberately has no Traefik mount) every
call is a no-op. Only the isolated ``route-syncer`` service sets the dir and
actually writes (see deploy/DOKPLOY.md).

Routers use ``Host(`<domain>`)`` with ``tls.certResolver=letsencrypt``: the
domain IS extractable from the rule, so Traefik opens an ACME (HTTP-01) order on
the ``web`` entrypoint and issues a real, public Let's Encrypt cert for the
client domain — auto-renewed. This is the direct-to-origin model: the client
points an A record straight at this origin (no Cloudflare for SaaS), so public
TLS terminates here at Traefik. (The agency's own apex/tenant routers in
docker-compose.yml stay on HostRegexp + the CF Origin cert, since
``sites.katek.app`` is still fronted by Cloudflare.) ``service: cms-web@docker``
is the Compose-label service (docker provider).
"""
import json
import logging
import os
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
        # Host(`<domain>`) + certResolver=letsencrypt: Traefik extracts the
        # domain from the rule and ACME-issues a real public LE cert (HTTP-01 on
        # the `web` entrypoint). Direct-to-origin — no Cloudflare for the client.
        routers[f"cms-cd-{cd.pk}"] = {
            "rule": f"Host(`{cd.domain}`)",
            "entryPoints": ["websecure"],
            "service": "cms-web@docker",
            "tls": {"certResolver": "letsencrypt"},
        }
        # Plain-HTTP (:80) router that 308s to HTTPS. Custom domains hit the
        # origin directly — unlike the CF-fronted agency hosts (where Cloudflare
        # upgrades http:// at the edge), there's no edge here, so without this
        # http://<domain> would 404. The ACME HTTP-01 challenge is unaffected:
        # Traefik serves /.well-known/acme-challenge/ on its own higher-priority
        # internal router before this one ever matches.
        routers[f"cms-cd-{cd.pk}-web"] = {
            "rule": f"Host(`{cd.domain}`)",
            "entryPoints": ["web"],
            "service": "cms-web@docker",
            "middlewares": ["cms-redirect-to-https"],
        }
    config = {"http": {"routers": routers}}  # routers: {} when empty — valid config
    if routers:
        # Self-contained redirect middleware — we don't reference Dokploy's
        # redirect-to-https@file (which could change). Only emitted when there's
        # a router to attach it to, so an empty verified set stays minimal.
        config["http"]["middlewares"] = {
            "cms-redirect-to-https": {
                "redirectScheme": {"scheme": "https", "permanent": True}
            }
        }
    return config


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
