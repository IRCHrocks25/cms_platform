from pathlib import Path
import os
import dj_database_url
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

_allowed_hosts_env = os.environ.get("DJANGO_ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_env.split(",") if h.strip()] or ["*"]

# In local dev, accept the localhost/lvh.me wildcard hosts used for tenant
# subdomain previews (no-op when ALLOWED_HOSTS is already "*").
if DEBUG:
    for _h in ("localhost", "127.0.0.1", ".localhost", TENANT_DEV_BASE_DOMAIN, f".{TENANT_DEV_BASE_DOMAIN}"):
        if _h and _h not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_h)

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
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",

    "core.middleware.TenantResolverMiddleware",
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
# Secure=True, which means plain-HTTP dev would lose its cookies — so this
# only kicks in when DEBUG is off. Cloudflare strips X-Frame-Options and
# adds Content-Security-Policy: frame-ancestors at the edge.
if DEBUG:
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SECURE = False
else:
    SESSION_COOKIE_SAMESITE = "None"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SAMESITE = "None"
    CSRF_COOKIE_SECURE = True


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

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_ANNOTATE_MODEL = os.environ.get("OPENAI_ANNOTATE_MODEL", "gpt-4o-mini")

RAILWAY_TOKEN = os.environ.get("RAILWAY_TOKEN", "")
RAILWAY_SERVICE_ID = os.environ.get("RAILWAY_SERVICE_ID", "")
RAILWAY_ENVIRONMENT_ID = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
RAILWAY_PROJECT_ID = os.environ.get("RAILWAY_PROJECT_ID", "")

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
