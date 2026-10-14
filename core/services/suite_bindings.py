"""Default-off suite create/link primitives; no HTTP route or key issuance.

Only a future privileged provisioner adapter may call these functions. The
caller must provide immutable IDs; names, email addresses and slugs never
prove ownership of an existing tenant.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q

from core.models import (
    SuiteFormBinding,
    SuiteTenantBinding,
    Tenant,
    validate_tenant_subdomain,
)
from core.services.accounts import create_tenant_account


class SuiteBindingConflict(ValueError):
    """An immutable suite ID, tenant, owner or request disagrees."""


def _uuid(value, field):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise SuiteBindingConflict(f"invalid {field}") from exc


def _operator(operator):
    if (
        operator is None
        or getattr(operator, "pk", None) is None
        or not get_user_model().objects.filter(
            pk=operator.pk, is_active=True, is_superuser=True
        ).exists()
    ):
        raise SuiteBindingConflict("verified operator required")


def _request_hash(mode, **fields):
    body = json.dumps({"mode": mode, **fields}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _replay(business_id, operation_key, request_hash):
    matching = list(
        SuiteTenantBinding.objects.filter(
            Q(business_id=business_id) | Q(operation_key=operation_key)
        )[:2]
    )
    if not matching:
        return None
    if len(matching) != 1:
        raise SuiteBindingConflict("business and operation refer to different sites")
    binding = matching[0]
    if (
        binding.business_id != business_id
        or binding.operation_key != operation_key
        or binding.request_hash != request_hash
    ):
        raise SuiteBindingConflict("suite request conflicts with immutable binding")
    if binding.tenant.owner_id != binding.owner_id:
        raise SuiteBindingConflict("bound tenant owner has changed")
    return binding


def create_suite_site(
    *, business_id, operation_key, name, subdomain, owner, html, operator
):
    """Create one unpublished site for an existing local owner; replay returns it."""
    _operator(operator)
    business_id = _uuid(business_id, "business_id")
    operation_key = _uuid(operation_key, "operation_key")
    name = (name or "").strip()
    subdomain = (subdomain or "").strip().lower()
    if not name or owner is None or owner.pk is None:
        raise ValueError("name and saved owner are required")
    validate_tenant_subdomain(subdomain)
    if subdomain in (getattr(settings, "TENANT_RESERVED_SUBDOMAINS", set()) or set()):
        raise SuiteBindingConflict("reserved subdomain")
    if not (html or "").strip():
        raise ValueError("nonempty draft HTML is required")
    digest = _request_hash(
        "created", name=name, subdomain=subdomain, owner_id=owner.pk,
        html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
    )
    existing = _replay(business_id, operation_key, digest)
    if existing is not None:
        return existing

    try:
        with transaction.atomic():
            # Serialize repeat calls for the same owner before tenant seeding.
            get_user_model().objects.select_for_update().get(pk=owner.pk)
            existing = _replay(business_id, operation_key, digest)
            if existing is not None:
                return existing
            if Tenant.objects.filter(subdomain=subdomain).exists():
                raise SuiteBindingConflict("subdomain belongs to an existing site")
            tenant, _, password = create_tenant_account(
                name=name, subdomain=subdomain, custom_domain="",
                username=None, email=None, owner_user=owner, html=html,
                is_published=False,
            )
            if password is not None:
                raise AssertionError("suite provisioning created a password")
            binding = SuiteTenantBinding.objects.create(
                business_id=business_id, operation_key=operation_key,
                tenant=tenant, owner=owner, mode=SuiteTenantBinding.MODE_CREATE,
                request_hash=digest, confirmed_by=operator,
            )
            SuiteFormBinding.objects.create(suite_binding=binding, is_active=False)
            return binding
    except IntegrityError as exc:
        existing = _replay(business_id, operation_key, digest)
        if existing is not None:
            return existing
        raise SuiteBindingConflict("site, business or operation already bound") from exc


def link_suite_site(
    *, business_id, operation_key, tenant_id, expected_owner_id, operator
):
    """Link an exact existing tenant after active superuser owner confirmation."""
    _operator(operator)
    business_id = _uuid(business_id, "business_id")
    operation_key = _uuid(operation_key, "operation_key")
    try:
        tenant_id = int(tenant_id)
        expected_owner_id = int(expected_owner_id)
    except (TypeError, ValueError) as exc:
        raise SuiteBindingConflict("exact tenant and owner IDs required") from exc
    digest = _request_hash(
        "linked", tenant_id=tenant_id, owner_id=expected_owner_id,
        operator_id=operator.pk,
    )
    existing = _replay(business_id, operation_key, digest)
    if existing is not None:
        return existing

    try:
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().filter(pk=tenant_id).first()
            if tenant is None or tenant.owner_id != expected_owner_id:
                raise SuiteBindingConflict("tenant owner verification failed")
            existing = _replay(business_id, operation_key, digest)
            if existing is not None:
                return existing
            if SuiteTenantBinding.objects.filter(tenant_id=tenant_id).exists():
                raise SuiteBindingConflict("tenant already bound")
            binding = SuiteTenantBinding.objects.create(
                business_id=business_id, operation_key=operation_key,
                tenant=tenant, owner_id=expected_owner_id,
                mode=SuiteTenantBinding.MODE_LINK, request_hash=digest,
                confirmed_by=operator,
            )
            SuiteFormBinding.objects.create(suite_binding=binding, is_active=False)
            return binding
    except IntegrityError as exc:
        existing = _replay(business_id, operation_key, digest)
        if existing is not None:
            return existing
        raise SuiteBindingConflict("tenant, business or operation already bound") from exc
