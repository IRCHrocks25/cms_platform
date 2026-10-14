"""Provisioner-owned binding and durable Sites native-form ingestion."""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from django.conf import settings
from django.core import signing
from django.db import transaction
from bs4 import BeautifulSoup

from core.models import BusinessFormBinding, FormDelivery, FormSubmission

FORM_TOKEN_SALT = "cms-platform-business-form-v1"
FORM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _cipher() -> Fernet:
    key = getattr(settings, "SITES_FORM_ENCRYPTION_KEY", "")
    if not key:
        raise ValueError("SITES_FORM_ENCRYPTION_KEY must be configured for form binding")
    return Fernet(key.encode() if isinstance(key, str) else key)


def reveal_signing_secret(binding: BusinessFormBinding) -> str:
    return _cipher().decrypt(binding.signing_secret_ciphertext.encode()).decode()


def bind_business(*, tenant, business_id, receiver_url: str, signing_secret: str) -> BusinessFormBinding:
    """Idempotent provisioning port. A second business may never take a site."""
    business_id = uuid.UUID(str(business_id))
    parsed_url = urlsplit(receiver_url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
        or bool(parsed_url.fragment)
        or len(receiver_url) > 600
        or not signing_secret
        or len(signing_secret) > 256
    ):
        raise ValueError("Business form receiver requires HTTPS and a signing secret")
    cipher = _cipher()
    binding, created = BusinessFormBinding.objects.get_or_create(
        tenant=tenant,
        defaults={
            "business_id": business_id,
            "receiver_url": receiver_url,
            "signing_secret_ciphertext": cipher.encrypt(signing_secret.encode()).decode(),
        },
    )
    if not created and (
        binding.business_id != business_id
        or binding.receiver_url != receiver_url
        or reveal_signing_secret(binding) != signing_secret
    ):
        raise ValueError("Site is already bound to a different business form receiver")
    return binding


def form_token(tenant, page, form_id: str) -> str:
    if not FORM_ID_RE.fullmatch(form_id):
        raise ValueError("Invalid form ID")
    if page is not None and page.tenant_id != tenant.pk:
        raise ValueError("Page does not belong to site")
    return signing.dumps(
        {"tenant": tenant.pk, "page": page.pk if page else None, "form": form_id},
        salt=FORM_TOKEN_SALT,
    )


def read_form_token(token: str, tenant):
    """Only the host-resolved tenant may redeem the signed page/form claim."""
    claim = signing.loads(token, salt=FORM_TOKEN_SALT, max_age=24 * 60 * 60)
    if not isinstance(claim, dict) or claim.get("tenant") != tenant.pk or not FORM_ID_RE.fullmatch(str(claim.get("form") or "")):
        raise signing.BadSignature("Form token tenant or form mismatch")
    return claim


def inventory_form_capture(html: str) -> dict[str, list[str]]:
    """Conservative page inventory for the later publication readiness gate."""
    soup = BeautifulSoup(html, "lxml")
    native = [
        str(el.get("data-cms-form")) for el in soup.find_all(attrs={"data-cms-form": True})
        if FORM_ID_RE.fullmatch(str(el.get("data-cms-form") or ""))
        and (
            el.name != "form"
            or (str(el.get("action") or "").endswith("/api/site-forms/submit/")
                and el.find("input", attrs={"name": "form_token"}) is not None)
        )
    ]
    unsupported = [
        str(el.get("src") or el.get("id") or el.name)
        for el in soup.find_all(["form", "iframe"])
        if not (el.name == "form" and el.get("data-cms-form") in native)
    ]
    return {"native": native, "unsupported": unsupported}


def queue_submission(
    *, tenant, page, form_id: str, email: str, name: str, phone: str,
    message: str, consent: bool, source: dict, event_id=None,
) -> FormSubmission:
    """Commit submission and outbox in one transaction before success is returned."""
    if not FORM_ID_RE.fullmatch(form_id):
        raise ValueError("Invalid form ID")
    if page is not None and page.tenant_id != tenant.pk:
        raise ValueError("Page does not belong to site")
    binding = BusinessFormBinding.objects.get(tenant=tenant)
    event_id = uuid.UUID(str(event_id)) if event_id else uuid.uuid4()
    with transaction.atomic():
        submission, created = FormSubmission.objects.get_or_create(
            event_id=event_id,
            defaults={
                "tenant": tenant, "business_id": binding.business_id,
                "page": page, "form_id": form_id, "email": email,
                "name": name, "phone": phone, "message": message,
                "consent": consent, "source": source,
            },
        )
        if not created and (
            submission.tenant_id != tenant.pk
            or submission.business_id != binding.business_id
            or submission.page_id != (page.pk if page else None)
            or submission.form_id != form_id
        ):
            raise ValueError("Event ID belongs to another site, page, or form")
        FormDelivery.objects.get_or_create(submission=submission)
    return submission
