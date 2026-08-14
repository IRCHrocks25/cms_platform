# Staging environment

A second, isolated deploy of this app for reviewing UI work before it reaches
clients. It runs on the **same Dokploy host as production**, which is the source
of every hazard documented here.

| | Production | Staging |
|---|---|---|
| Dokploy project | `cms-dashboard` | `cms-dashboard` (same) |
| Environment | `production` (`RTqjeg8in9kGVOmJ6L_Cc`) | `staging` (`MzPbhgIhn6vzLJWBH5Clq`) |
| Compose file | `docker-compose.yml` | `docker-compose.staging.yml` |
| Branch tracked | `main` | `main` |
| Docker image tag | `cms-platform:latest` | `cms-platform-staging:latest` |
| Host | `sites.katek.app` + `*.sites.katek.app` | `staging.sites.katek.app` |
| Database | production Postgres | `cms-staging-db`, own container |
| route-syncer | runs | **absent** |
| Custom domains | work | do not work, by design |
| Outbound email | Resend live | disabled (blank key) |
| Media uploads | Iceberg CDN, tenant `t1` | local volume |

## The three things that must never be copied from production

**1. The image tag.** Production's compose declares `image: cms-platform:latest`
alongside its `build:` block, so a build there re-tags that name. If staging
built under the same tag, the next production container restart would come up on
the staging image without anyone deploying it. Staging uses
`cms-platform-staging:latest`.

**2. Traefik names.** Traefik's router, service and middleware names are global
on this host. Production hardcodes `cms-web`, `cms-apex`, `cms-tenants`,
`cms-apex-web`, `cms-tenants-web` and `cms-redirect-to-https`. Reusing any of
them merges staging's containers into production's load-balancer pool — real
client traffic served by staging code. Staging prefixes everything `cmsstg-`.

**3. The route-syncer.** `core/services/traefik_routes.py:40` sets
`ROUTES_FILENAME = "custom-domains.yml"` — one fixed filename in the shared
`/etc/dokploy/traefik/dynamic`. Two syncers against two databases means the
last writer wins, so a staging syncer would delete every verified client custom
domain from Traefik's config. Staging runs **no** syncer and mounts **no**
Traefik directory. `TRAEFIK_DYNAMIC_DIR` is left unset; `_dynamic_dir()` returns
`None` and the writer refuses, so even a manual `sync_traefik_routes` is inert.

## Why `staging.sites.katek.app` and not a wildcard

TLS at the origin is the Cloudflare Origin CA cert in Traefik's default store,
covering `*.sites.katek.app`. That is a **single** label of wildcard:
`staging.sites.katek.app` is covered, `acme.staging.sites.katek.app` is not.

So staging serves the agency dashboard and the tenant editor, both same-host —
including the editor's preview iframe, which loads
`/dashboard/sites/<id>/preview/` on the same origin. What it cannot serve is a
tenant's *public* site on its own subdomain. Verify those locally with
`manage.py runserver` (`acme.localhost:8000` resolves per RFC 6761) or on
production.

Giving staging real tenant subdomains means a DNS-01 wildcard cert for
`*.staging.sites.katek.app`. Not done; not needed for UI review.

The router is `HostRegexp`, not `Host`, for the same reason production is: the
`websecure` entrypoint defaults to `certResolver=letsencrypt`, and a `tls=true`
router with an extractable domain inherits it and tries to ACME-issue. Priority
is 200 so it outranks production's `cms-tenants` wildcard (priority 10), which
matches `staging.sites.katek.app` too — `staging` is a valid `[a-z0-9-]+` label.

## First deploy

The database starts empty. After the first deploy, seed it:

```bash
# from the Dokploy UI: staging → sites → Terminal, or via docker exec
python manage.py migrate          # runs automatically via entrypoint.sh
python manage.py createsuperuser
python manage.py seed_demo_data   # idempotent, staging-only sample content
```

The demo seeder creates visibly namespaced templates, sites, pages, posts, and
team members for UI review. Re-run it safely after a staging reset; it updates
its deterministic manifest without duplicating rows. Remove only that manifest
with `python manage.py seed_demo_data --clear`. Both operations refuse to run
unless `ALLOW_DEMO_SEED=1`, which this staging compose sets and production does
not.

Then sign in at `https://staging.sites.katek.app/login/`. **Do not restore a
production dump into staging** — it carries real client content and real user
rows, and the app cannot send them mail from here but can still show their data
to anyone with the staging URL.

## Deploying a branch

Staging tracks `main` with autoDeploy on, so a merge to `main` lands here first —
production deploys only when someone triggers it (`autoDeploy` is off there).
Staging is therefore the place to catch a bad merge before it reaches clients.

To review a branch before merging, point staging at it: change the branch on the
`sites-staging` compose service in Dokploy, redeploy, and set it back to `main`
when you're done. That was how it ran during the UI overhaul
(`feat/cms-ui-overhaul`, PR #30).

## Tearing it down

Delete the `staging` environment in the `cms-dashboard` project. That removes the
compose service and the `cms-staging-db` Postgres together. Production is
unaffected — it shares no volume, no database, no Traefik router, and no image
tag with staging.
