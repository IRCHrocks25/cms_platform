# Dokploy / Traefik deployment runbook

How the CMS is wired behind Dokploy's bundled Traefik, why the routing labels
look the way they do, and how to recover if the deployment model changes.

Companion files:
- `../docker-compose.yml` — the Compose service (`web`) + Traefik routing labels
- `traefik/origin-cert.yml` — Cloudflare Origin Cert as a Traefik dynamic file

---

## Topology

- Dokploy **COMPOSE** service, stack id `cmsdashboard-sites-2ka9w7`.
- One container, service named `web` in Compose, `expose: 8000`, attached to the
  external `dokploy-network`.
- Public TLS terminates at the **Cloudflare edge**. Cloudflare → origin is the
  only hop our Origin Certificate has to satisfy.
- Tenant routing is by **Host header** — `core/middleware.py::TenantResolverMiddleware`
  resolves `request.tenant` from the leftmost label vs `TENANT_BASE_DOMAIN`
  (`sites.katek.app`).

```
  Cloudflare edge (TLS, ACM advanced cert: sites.katek.app + *.sites.katek.app)
        │  edge → origin hop, SSL mode = Full
        ▼
  Dokploy Traefik (tls=true, default-store = CF Origin Cert)
        │  routers below, all → service cms-web
        ▼
  web container :8000  (Django, resolves tenant from Host header)
```

---

## Traefik version (this host)

> **Record the exact version here.** Run on the Dokploy host:
> ```
> docker exec dokploy-traefik traefik version
> ```
> Expected: a v3 release **older than 3.7.0** (Dokploy bundles ~3.6.x).
>
> **Measured version: `traefik:v3.6.x`** (Dokploy-bundled). Pin the exact patch
> tag from the Dokploy Docker page here if needed; what's load-bearing is the
> minor — **3.6 < 3.7.0**, which is why the tenant/apex routers use HostRegexp
> rather than the wildcard `Host()` matcher (added in 3.7.0).

This version number is load-bearing for the routing choice below.

---

## Why HostRegexp for the tenant wildcard (not `Host(`*.…`)`)

Single-level wildcard matching in the `Host()` / `HostSNI()` matchers was added
in **Traefik v3.7.0**. So `Host(`*.sites.katek.app`)` *is* valid v3 syntax — but
only on >= 3.7.0. This host runs an **older v3** (see version above), where the
wildcard `Host()` form would not match. We therefore use:

```
HostRegexp(`^[a-z0-9-]+\.sites\.katek\.app$`)
```

which works on **all** of v3.

**We keep HostRegexp even if Traefik is later upgraded to >= 3.7.0.** The only
real advantage of the wildcard `Host()` matcher is the ability to attach
**per-router TLS options**, and we don't do that — we serve a single
default-store cert (the CF Origin Cert), not per-router TLS options. Switching to
`Host(`*.sites.katek.app`)` after a 3.7+ upgrade is therefore **optional and
cosmetic**, never required. If you do switch, the wildcard is single-level only
(matches `acme.sites.katek.app`, not `a.b.sites.katek.app`) — same as our regex.

### Reserved subdomains
HostRegexp compiles to RE2, which has **no negative lookahead**, so reserved
subdomains (`www`, `app`, `api`, `admin`, `dashboard`, `static`, `media`, `mail`)
can't be excluded inside the pattern. They nominally match the tenant router but
route to the same `cms-web` service, and Django's middleware maps them to the
agency surface (`request.tenant = None`). The day one needs a *different* backend,
add an exact `Host()` router for it — its higher priority outranks the
`priority=10` wildcard. Nothing to change in the regex.

---

## The routers (and why we own `cms-web`)

All three routers point at a service **we declare ourselves**:

```
traefik.http.services.cms-web.loadbalancer.server.port=8000
```

| Router       | Rule                                            | Priority |
|--------------|-------------------------------------------------|----------|
| `cms-apex`   | `HostRegexp(`^sites\.katek\.app$`)`             | 100      |
| `cms-tenants`| `HostRegexp(`^[a-z0-9-]+\.sites\.katek\.app$`)` | 10       |

There is **no** `.+` catch-all router (shared host — see "Custom client domains"
below). Custom client domains are routed by per-domain routers in a dynamic file,
not by a compose label.

**Both routers use HostRegexp on purpose** — see "Default certResolver" below.
The apex would naturally be `Host(`sites.katek.app`)`, but that form is avoided
deliberately.

**Why a self-declared service name:** if every router instead referenced a
Dokploy-/Compose-generated service name, that name is an implementation detail of
how Dokploy names containers — it can change between deploys or Dokploy versions,
silently breaking routing. By declaring `cms-web` ourselves in the labels and
pointing all routers at `service=cms-web`, the wiring is stable and
version-controlled. There is exactly one place the backend port lives.

**Priorities:** higher wins. Apex (exact host, 100) beats the tenant wildcard
(10) beats the custom-domain catch-all (1). The catch-all only fires for hosts
that are neither the apex nor a `*.sites.katek.app` subdomain — i.e. verified
client custom domains arriving via Cloudflare for SaaS.

---

## Origin certificate (no ACME at the origin)

We do **not** run ACME / a `certresolver` at the origin. Cloudflare terminates
public TLS at the edge; the origin only needs a cert the edge trusts on the
Full-mode hop. We install the **Cloudflare Origin Certificate** (covers
`sites.katek.app` + `*.sites.katek.app`) as both a named cert and the Traefik
**default store** cert via `traefik/origin-cert.yml`.

Install steps and the in-container mount verification are documented at the top of
`traefik/origin-cert.yml`. The routers set `tls=true` with **no certresolver**, so
Traefik serves the default-store cert for every matched host.

### Default certResolver on websecure (why even the apex is HostRegexp)

The live `traefik.yml` sets a **default `certResolver: letsencrypt`** on the
`websecure` entrypoint. A `tls=true` router with **no explicit resolver** inherits
that default and then extracts cert SANs **from its rule**:

- `Host(`sites.katek.app`)` → extractable domain → Traefik opens an **ACME order**
  for `sites.katek.app`. We do **not** want Let's Encrypt at the origin.
- `HostRegexp(...)` → **no** extractable domain → Traefik can't form an ACME order
  → falls back to the **default-store cert** (the CF Origin CA). ✅

That's why all three routers — including the apex — use HostRegexp. We never set a
per-router resolver and never edit `traefik.yml`. **Post-deploy, confirm no ACME
fired and the right cert is served:**

```bash
docker logs dokploy-traefik 2>&1 | grep -iE 'acme|sites\.katek\.app'   # expect NO ACME order
curl -sv https://sites.katek.app 2>&1 | grep -iE 'subject:|issuer:'    # issuer = Cloudflare Origin CA
```

### Provider: docker vs swarm (where the labels must live)

The live `traefik.yml` enables **both** the `docker` and `swarm` providers. Which
one reads our labels depends on how Dokploy materializes this COMPOSE stack:

- `docker compose up` → **standalone containers**: the docker provider reads the
  **top-level `labels:`** (our current layout). Correct as-is.
- `docker stack deploy` → **Swarm service**: the swarm provider reads
  **service-level `deploy.labels:`** only; top-level `labels:` are ignored and the
  routers never register.

Dokploy COMPOSE normally deploys standalone containers, so top-level labels are
expected to work — but confirm after every (re)deploy:

```bash
docker exec dokploy-traefik wget -qO- http://localhost:8080/api/http/routers \
  | grep -o '"name":"cms-[^"]*"'        # expect cms-apex@docker, cms-tenants@docker (custom-domain routers come from the dynamic file, named cms-cd-<pk>@file)
```

If nothing comes back, it deployed as a Swarm service: move every
`traefik.*` label from `labels:` to `deploy.labels:` in `docker-compose.yml`.
Do **not** put the labels in both places — with both providers watching, that can
double-register router names and error.

---

## Custom client domains (no catch-all on a shared host)

This Dokploy host is **shared** with other stacks (e.g. `businesscenter-ibc`,
`haptic-hard-drive`, `demo-acme`). So we deliberately do **not** run a
`HostRegexp(`.+`)` catch-all — it would make this app the default backend for
every otherwise-unmatched host on the box. Instead, each verified custom client
domain gets its **own** router in a Traefik dynamic file.

**Source of truth:** the `CustomDomain` table (`is_verified=True`). The file is
regenerated wholesale from the DB — never edited by hand, never patched
incrementally.

**Who writes it:** the isolated **`route-syncer`** compose service, and only it.
- It mounts the dynamic-config **root** `/etc/dokploy/traefik/dynamic` and writes
  `custom-domains.json` there atomically. (We *wanted* a confined
  `cms-custom-domains/` subdir, but Traefik's file provider watches `directory:`
  **non-recursively** on this host — a subdir file is never read — so the file
  must live in the root.)
- Because the mount now spans the whole dynamic dir, the writer is confined by
  **code, not mount scope**: `traefik_routes.py` only ever creates/replaces its
  own `custom-domains.json` (plus its own `.custom-domains.*` temp file) via an
  atomic same-dir rename. It never enumerates, modifies, or deletes sibling files
  (`dokploy.yml`, `middlewares.yml`, `traefik.yml`, `acme.json`,
  `origin-cert.yml`).
- The **web** container has **no** Traefik mount, so an internet-facing app
  compromise has no path to Traefik config. The syncer serves no traffic
  (`traefik.enable=false`, no ports) and only reads the DB + writes one file.
- It loops every 60s; `core/services/traefik_routes.py` skips the write when the
  verified set is unchanged, so an idle loop causes no Traefik reloads.

**Emitted routers** (`core/services/traefik_routes.py`):
```json
"cms-cd-<pk>": {
  "rule": "HostRegexp(`^<escaped-domain>$`)",
  "entryPoints": ["websecure"],
  "service": "cms-web@docker",
  "tls": {}
}
```
`HostRegexp` (not `Host()`) for the same reason as the apex: it exposes no
extractable domain, so the default `letsencrypt` resolver can't ACME-issue for
the client domain — Traefik serves the default-store CF Origin CA cert, correct
under Cloudflare SSL=Full. Keyed by `<pk>` because slugified domains can collide.

**Denylist safety net:** with the catch-all gone, a stray or hostile verified
`CustomDomain` row is the only way a wrong router could appear. `traefik_routes.py`
hard-skips (skip + log) any row whose host is our own infrastructure —
`katek.app`, `sites.katek.app`, `proxy.sites.katek.app`, `dokploy.katek.app`, and
(dynamically) the tenant base domain plus all of its subdomains. Such a row can
never emit a router.

**Container facts the syncer relies on:** the image has **no `USER`** so it runs
as **root** and can write the root-owned dynamic dir. The
syncer **overrides `entrypoint.sh`** so it does *not* run `migrate` — `web` owns
migrations, and running them in both containers would race (concurrent DDL) on
first deploy. The sync command is loop-resilient: a pre-migration DB error just
logs and retries on the next tick.

**Operator: immediate sync after onboarding** (skip the ≤60s loop wait):
```bash
docker exec <route-syncer container> python manage.py sync_traefik_routes
```
The same command does the **first populate** on initial deploy and recovers from
any drift. The `web` container's copy of the command is a no-op (no
`TRAEFIK_DYNAMIC_DIR`, no mount) — run it in the syncer.

**End-to-end custom-domain flow:** operator adds the domain → `cloudflare.py`
registers the CF for SaaS custom hostname (TXT/DCV) → client publishes the CNAME
+ `_acme-challenge` records → operator clicks verify → CF reports active →
`is_verified` flips → within ≤60s the syncer emits the router → traffic for that
host reaches `cms-web`, and `TenantResolverMiddleware` maps the host to the tenant
via the `CustomDomain` table. No per-client Dokploy step.

---

## Recovery: if we ever switch from COMPOSE to a Dokploy Application

A Dokploy **Application** (as opposed to a raw Compose service) generates its own
Traefik labels and service name, and may not let you hand-write the labels above.
If that switch ever happens:

1. **Find the real generated service name** Traefik sees:
   ```
   docker exec dokploy-traefik wget -qO- http://localhost:8080/api/http/services \
     | grep -o '"name":"[^"]*"'
   ```
   (or inspect routers: `.../api/http/routers`).
2. **Either** repoint our routers at that name, **or** (preferred) re-add the
   explicit `traefik.http.services.cms-web.loadbalancer.server.port=8000` label so
   you keep the stable `cms-web` name and don't depend on the generated one.
3. Re-attach both routers (`cms-apex`, `cms-tenants`) with the rules/priorities in
   the table above, pointing at `cms-web`. (There is no `cms-custom` label —
   custom domains stay in the `route-syncer` dynamic file, which references
   `cms-web@docker` and is unaffected by the service-name change as long as the
   service is still named `cms-web`.)
4. Confirm the Origin Cert dynamic file is still mounted and is still the default
   store cert.

The whole point of the `cms-web` indirection is that step 2's "preferred" path
makes routing independent of however Dokploy names containers.

---

## Quick verification commands (on the Dokploy host)

```bash
# Traefik version (record above)
docker exec dokploy-traefik traefik version

# Every router rule Traefik currently knows (spot our cms-* routers; check for
# strays like a leftover sslip.io test domain on the letsencrypt certresolver)
docker exec dokploy-traefik wget -qO- http://localhost:8080/api/http/routers \
  | grep -o '"rule":"[^"]*"' | sort -u

# Origin cert files resolve inside the container
docker exec dokploy-traefik ls -l /etc/dokploy/traefik/dynamic/certs/
docker inspect dokploy-traefik --format '{{json .Mounts}}'
```
