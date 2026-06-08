from pathlib import Path
import os
import dj_database_url
from django.utils.functional import lazy
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-secret-change-me-in-production",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

_tenant_base_domains = [
    domain.strip().lstrip(".")
    for domain in os.environ.get("TENANT_BASE_DOMAIN", "localhost").split(",")
    if domain.strip()
]
TENANT_BASE_DOMAIN = _tenant_base_domains[0] if _tenant_base_domains else "localhost"
TENANT_ADDITIONAL_BASE_DOMAINS = _tenant_base_domains[1:]
# kept for backwards-compat with existing references
_additional_tenant_base_domains = TENANT_ADDITIONAL_BASE_DOMAINS

# Local-dev wildcard base. `lvh.me` (and any `*.lvh.me`) publicly resolves to
# 127.0.0.1, so `acme.lvh.me:8000` reaches the local server with subdomain
# tenant routing working — no hosts-file edits needed. Only honored in DEBUG
# (see TenantResolverMiddleware + tenant_public_url); production is untouched.
TENANT_DEV_BASE_DOMAIN = os.environ.get("TENANT_DEV_BASE_DOMAIN", "lvh.me").strip(".").lower()

# ALLOWED_HOSTS = ["*"] is deliberate. Client custom domains are dynamic — added
# and verified at runtime via the CustomDomain table — so the set of valid hosts
# cannot be enumerated at deploy time. The host allowlist is enforced at the EDGE,
# not in Django:
#   - Traefik has NO catch-all router. It forwards only the apex (`sites.katek.app`),
#     the tenant wildcard (`*.sites.katek.app`), and one router per VERIFIED
#     CustomDomain (emitted to custom-domains.yml by the route-syncer). Any other
#     Host is dropped before it ever reaches this app.
#   - Cloudflare fronts the origin (SSL=Full); requests arrive through the edge.
#   - TenantResolverMiddleware maps only recognized hosts (known subdomains /
#     verified custom domains) to a tenant; an unrecognized host resolves to no
#     tenant and never serves tenant content.
# A second static allowlist in Django would only re-check that edge-vetted set and
# would 400 every newly-onboarded custom domain until a redeploy. So host
# restriction lives at the edge (see deploy/DOKPLOY.md). NOTE: the DJANGO_ALLOWED_HOSTS
# env var is intentionally no longer consulted.
ALLOWED_HOSTS = ["*"]

# Behind Traefik / Cloudflare — trust the X-Forwarded-Proto header so Django
# knows requests are HTTPS even though the inner hop is plain HTTP.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

TENANT_RESERVED_SUBDOMAINS = {
    "www", "app", "api",
    "admin", "dashboard", "static", "media", "mail",
}

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
if TENANT_BASE_DOMAIN:
    _csrf_domains = [TENANT_BASE_DOMAIN, *_additional_tenant_base_domains]
    CSRF_TRUSTED_ORIGINS.extend(
        list(
            dict.fromkeys(
                origin
                for domain in _csrf_domains
                for origin in (
                    f"https://{domain}",
                    f"https://*.{domain}",
                    f"http://{domain}",
                    f"http://*.{domain}",
                )
            )
        )
    )
CSRF_TRUSTED_ORIGINS.extend(
    [
        "https://sites.katek.app",
        "https://*.sites.katek.app",
        "http://sites.katek.app",
        "http://*.sites.katek.app",
    ]
)
if DEBUG and TENANT_DEV_BASE_DOMAIN:
    CSRF_TRUSTED_ORIGINS.extend(
        [
            f"http://{TENANT_DEV_BASE_DOMAIN}:8000",
            f"http://*.{TENANT_DEV_BASE_DOMAIN}:8000",
            f"http://{TENANT_DEV_BASE_DOMAIN}",
            f"http://*.{TENANT_DEV_BASE_DOMAIN}",
        ]
    )
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(CSRF_TRUSTED_ORIGINS))

# --- Custom-domain CSRF trust (dynamic) -------------------------------------
# The wildcard origins above (e.g. https://*.sites.katek.app) cover tenant
# SUBDOMAINS but NOT client custom domains, which are arbitrary and only known at
# runtime (verified CustomDomain rows). We derive their trusted origins from the DB.
#
# The common case already works WITHOUT this entry: login and the editor POST to
# the SAME custom domain that served the page, and Django accepts a same-origin
# request via its Origin/Referer check as soon as get_host() succeeds (which it
# now does — ALLOWED_HOSTS=["*"]). CSRF_TRUSTED_ORIGINS only matters for
# CROSS-origin POSTs; we register verified custom domains so those work too.
#
# Evaluated LAZILY so the DB query runs at request time, never at settings-import
# / app-load time (a model query there raises AppRegistryNotReady). Caveat: the
# result is cached per worker, so a domain verified after a worker started is
# trusted only once that worker recycles. That's fine in practice because
# same-origin POST works regardless; a domain needing CROSS-origin trust
# immediately may still need per-domain handling (worker restart or an explicit
# origin entry).
_CSRF_STATIC_ORIGINS = list(CSRF_TRUSTED_ORIGINS)


def _csrf_trusted_origins():
    origins = list(_CSRF_STATIC_ORIGINS)
    try:
        from core.models import CustomDomain

        for domain in (
            CustomDomain.objects.filter(is_verified=True).values_list(
                "domain", flat=True
            )
        ):
            origins.append(f"https://{domain}")
            origins.append(f"http://{domain}")
    except Exception:
        # DB not ready (early boot / pre-migrate) — fall back to static origins.
        pass
    return list(dict.fromkeys(origins))


CSRF_TRUSTED_ORIGINS = lazy(_csrf_trusted_origins, list)()


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",

    "core",
    "dashboard",
]


MIDDLEWARE = [
    # MUST stay outermost (first entry) so its response phase runs LAST,
    # after Session/Csrf have actually written their cookies. Putting it
    # below them makes it run first on response (when response.cookies
    # is still empty for sessionid/csrftoken) and the Partitioned attribute
    # never gets attached. See core/middleware.py docstring.
    "core.middleware.PartitionedCookieMiddleware",

    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",

    "core.middleware.TenantResolverMiddleware",
    "core.middleware.DiagnosticHeaderMiddleware",
    "core.middleware.FrameAncestorsCspMiddleware",
]


ROOT_URLCONF = "cms_platform.urls"


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


WSGI_APPLICATION = "cms_platform.wsgi.application"


DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
    )
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"


X_FRAME_OPTIONS = "SAMEORIGIN"


# Cross-origin iframe embedding (e.g. sites.katek.app loaded inside a
# white-labeled GHL dashboard). Browsers refuse SameSite=Lax cookies in a
# cross-origin iframe, so the session never sticks. SameSite=None requires
# Secure=True (HTTPS), so it's gated on an explicit env var. Set
# IFRAME_EMBED=1 in production; leave unset (or 0) in local dev where
# plain HTTP would otherwise discard the cookies. Cloudflare strips
# X-Frame-Options and adds Content-Security-Policy: frame-ancestors at
# the edge.
IFRAME_EMBED = os.environ.get("IFRAME_EMBED", "0") == "1"
if IFRAME_EMBED:
    SESSION_COOKIE_SAMESITE = "None"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SAMESITE = "None"
    CSRF_COOKIE_SECURE = True
else:
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SECURE = False

# When the embed flow redirects from the agency host into a tenant subdomain
# (e.g. sites.katek.app/embed/ → dalto-ai-advisor.sites.katek.app/dashboard/),
# the session cookie must span subdomains. Set COOKIE_PARENT_DOMAIN=.sites.katek.app
# in production. Leave unset locally — runserver only listens on one host.
_cookie_parent_domain = os.environ.get("COOKIE_PARENT_DOMAIN", "").strip()
if _cookie_parent_domain:
    SESSION_COOKIE_DOMAIN = _cookie_parent_domain
    CSRF_COOKIE_DOMAIN = _cookie_parent_domain


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "loggers": {
        "core": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "dashboard": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}


CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ZONE_ID = os.environ.get("CLOUDFLARE_ZONE_ID", "")
CLOUDFLARE_DCV_DELEGATION_TARGET = "711b5e8ed3b3aa16.dcv.cloudflare.com"

# Directory where the custom-domain Traefik dynamic file is written. Only the
# isolated `route-syncer` compose service sets this (and mounts the dir); the web
# container leaves it empty, so route-writing is a no-op there. See
# core/services/traefik_routes.py + deploy/DOKPLOY.md.
TRAEFIK_DYNAMIC_DIR = os.environ.get("TRAEFIK_DYNAMIC_DIR", "")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_ANNOTATE_MODEL = os.environ.get("OPENAI_ANNOTATE_MODEL", "gpt-4o-mini")
# Per-request timeout (seconds) for the OpenAI annotation call. Keep this BELOW
# the Gunicorn worker --timeout (180s) so a slow/hung API surfaces as a clean
# AnnotatorError (JSON 502) instead of a killed worker (HTML 502 from the proxy).
OPENAI_TIMEOUT = float(os.environ.get("OPENAI_TIMEOUT", "120"))

RAILWAY_TOKEN = os.environ.get("RAILWAY_TOKEN", "")
RAILWAY_SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "")
RAILWAY_ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
RAILWAY_PROJECT_ID = os.environ.get("RAILWAY_PROJECT_ID", "")

# --------------------------------------------------------------------------- #
# GHL (GoHighLevel) marketplace app integration.                              #
# Embedded as a Custom Page inside a sub-account: GHL loads /embed/ in an     #
# iframe and passes ?location_id= and ?email= (from Custom Menu Link template #
# substitution). When GHL_AUTO_LOGIN=1, that view auto-logs the matched user. #
# CLIENT_ID/SECRET are used later for the proper marketplace OAuth flow.      #
# --------------------------------------------------------------------------- #
GHL_AUTO_LOGIN = os.environ.get("GHL_AUTO_LOGIN", "0") == "1"
GHL_CLIENT_ID = os.environ.get("GHL_CLIENT_ID", "")
GHL_CLIENT_SECRET = os.environ.get("GHL_CLIENT_SECRET", "")
# Shared/SSO secret used to decrypt the iframe context GHL sends via
# postMessage. Required only for the signed-context path (Phase 2).
GHL_SHARED_SECRET = os.environ.get("GHL_SHARED_SECRET", "")
# Comma-separated list of origins allowed to embed us in a frame
# (e.g. "https://app.industryrockstars.ch,https://app.daltoleadsystem.com").
# Pass "*" to allow any parent — only safe once /embed/ verifies signed
# context (Phase 2), not while it still trusts URL params (Phase 1).
GHL_FRAME_ANCESTORS = os.environ.get(
    "GHL_FRAME_ANCESTORS",
    "https://app.gohighlevel.com,https://*.gohighlevel.com,https://*.msgsndr.com",
)

# --------------------------------------------------------------------------- #
# Email — sent via the Resend HTTP API (see core/email_backend.py).            #
# --------------------------------------------------------------------------- #
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
# Prefer RESEND_FROM_EMAIL; fall back to DEFAULT_FROM_EMAIL from the environment.
DEFAULT_FROM_EMAIL = (
    os.environ.get("RESEND_FROM_EMAIL")
    or os.environ.get("DEFAULT_FROM_EMAIL")
    or "noreply@example.com"
)
EMAIL_BACKEND = "core.email_backend.ResendBackend"

# Password-reset tokens (default_token_generator) — signed + expiring.
PASSWORD_RESET_TIMEOUT = int(os.environ.get("PASSWORD_RESET_TIMEOUT", 60 * 60))  # 1h

# --------------------------------------------------------------------------- #
# Cloudinary — client media (images server-routed; video signed direct upload) #
# --------------------------------------------------------------------------- #
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")
# Validate-at-the-door limits for client uploads.
MEDIA_ALLOWED_IMAGE_FORMATS = {"png", "jpg", "jpeg", "gif", "webp"}
MEDIA_MAX_IMAGE_BYTES = int(os.environ.get("MEDIA_MAX_IMAGE_BYTES", 10 * 1024 * 1024))      # 10 MB
MEDIA_MAX_VIDEO_BYTES = int(os.environ.get("MEDIA_MAX_VIDEO_BYTES", 200 * 1024 * 1024))     # 200 MB
MEDIA_MAX_VIDEO_DURATION = int(os.environ.get("MEDIA_MAX_VIDEO_DURATION", 180))             # seconds
