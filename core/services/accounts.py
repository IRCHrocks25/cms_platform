import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.crypto import get_random_string

from core.models import (
    CustomDomain,
    Template,
    TemplateVersion,
    Tenant,
    TenantMembership,
    SuiteTenantBinding,
    validate_tenant_subdomain,
)
from core.services import custom_domains
from core.services.templates import assign_template


User = get_user_model()
logger = logging.getLogger(__name__)

PASSWORD_ALPHABET = (
    "abcdefghjkmnpqrstuvwxyz"
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "23456789"
)


class CustomDomainError(Exception):
    """Raised when a custom domain passed to ``create_tenant_account`` is
    invalid or already registered (CMS-37)."""


class AccountSeedError(Exception):
    """Raised when the template seed for ``create_tenant_account`` is missing
    or ambiguous (CMS-38). Exactly one of ``template``, ``html``, or
    ``new_template`` must be supplied."""


class OwnerTransferError(Exception):
    """The requested owner removal has no safe, unambiguous successor."""


def _locked_membership(tenant_id: int, membership_id: int):
    """Lock the site first, matching the owner-transfer write order."""
    try:
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        membership = (
            TenantMembership.objects.select_for_update()
            .select_related("user")
            .get(pk=membership_id, tenant=tenant)
        )
    except (Tenant.DoesNotExist, TenantMembership.DoesNotExist) as exc:
        raise OwnerTransferError("This site membership no longer exists.") from exc
    return tenant, membership


@transaction.atomic
def remove_noncanonical_membership(tenant_id: int, membership_id: int) -> str:
    """Remove a member while preserving the canonical owner's access."""
    tenant, membership = _locked_membership(tenant_id, membership_id)
    if membership.user_id == tenant.owner_id:
        raise OwnerTransferError("Transfer site ownership before removing this Owner.")
    username = membership.user.username
    membership.delete()
    return username


@transaction.atomic
def set_membership_role(tenant_id: int, membership_id: int, role: str) -> str:
    """Change a role without ever downgrading the canonical Owner."""
    if role not in dict(TenantMembership.ROLE_CHOICES):
        raise OwnerTransferError("Unknown role.")
    tenant, membership = _locked_membership(tenant_id, membership_id)
    if membership.user_id == tenant.owner_id and role != TenantMembership.ROLE_OWNER:
        raise OwnerTransferError("Transfer site ownership before changing this Owner's role.")
    membership.role = role
    membership.save(update_fields=["role"])
    return membership.user.username


@transaction.atomic
def transfer_and_remove_owner(
    tenant_id: int,
    target_membership_id: int,
    successor_membership_id: int,
    *,
    actor_id: int,
) -> User:
    """Transfer the canonical owner and remove the former owner's access.

    Lock and re-read the tenant and its memberships so a stale confirmation
    cannot silently transfer ownership to a different member. The whole write
    rolls back if the membership deletion fails.
    """
    try:
        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
    except Tenant.DoesNotExist as exc:
        raise OwnerTransferError("This site no longer exists.") from exc

    memberships = list(
        TenantMembership.objects.select_for_update()
        .select_related("user")
        .filter(tenant=tenant)
        .order_by("pk")
    )
    by_id = {membership.pk: membership for membership in memberships}
    target = by_id.get(target_membership_id)
    successor = by_id.get(successor_membership_id)
    owners = [membership for membership in memberships
              if membership.role == TenantMembership.ROLE_OWNER]

    if (
        target is None
        or target.user_id != tenant.owner_id
        or target.role != TenantMembership.ROLE_OWNER
    ):
        raise OwnerTransferError("The selected member is no longer the site owner.")
    if len(owners) != 2 or not all(m.user.is_active for m in owners):
        raise OwnerTransferError(
            "Transfer and remove requires exactly two active Owner members."
        )
    if successor is None or successor.pk == target.pk or successor not in owners:
        raise OwnerTransferError("The chosen successor is no longer an Owner member.")

    # A Suite binding records an immutable owner. Keep the tenant lock held so
    # linking and transfer cannot race, and refuse drift before either write.
    if SuiteTenantBinding.objects.filter(tenant_id=tenant.pk).exists():
        raise OwnerTransferError(
            "This site is linked to a suite business; ownership transfer requires a reviewed rebinding flow."
        )

    tenant.owner = successor.user
    tenant.save(update_fields=["owner"])
    target.delete()
    logger.info(
        "cms_owner_transferred_and_removed actor_id=%s tenant_id=%s "
        "former_user_id=%s successor_user_id=%s",
        actor_id, tenant.pk, target.user_id, successor.user_id,
    )
    return successor.user


def generate_password():
    return get_random_string(length=16, allowed_chars=PASSWORD_ALPHABET)


def create_tenant_account(
    *,
    name,
    subdomain,
    custom_domain,
    username,
    email,
    owner_user=None,
    template=None,
    html=None,
    new_template=None,
    is_published=False,
):
    """Create a tenant, owner login, and owner membership atomically.

    Seed the home template with exactly one of:

    - ``template``: library (or already-resolved) Template to clone
    - ``html``: raw HTML string; creates a tenant-owned template named after
      the site (CMS-38). Unannotated HTML correctly yields an empty schema /
      ``raw`` editing mode via ``Template.save``
    - ``new_template``: kwargs for an inline Template (dashboard new-client
      path); richer than ``html`` when a custom name/description is needed

    The generated password is returned to the caller and is never persisted
    outside Django's password hash. Suite provisioning may pass an existing
    ``owner_user`` instead, which creates no new person or password.

    ``is_published`` defaults to False so a brand-new client site starts as a
    draft: the operator (or client) publishes deliberately once content is
    ready, matching the "Publish, then visit public" smoke test. MCP
    ``create_client_account`` already passed False (A6).

    If ``custom_domain`` is supplied, a real (unverified) ``CustomDomain`` row
    is created for it via ``core.services.custom_domains.add_custom_domain``,
    that's the table the middleware and route-syncer actually key off, so a
    domain passed here now does something (CMS-37). The domain is checked
    *before* any row is created so an invalid/taken domain raises
    ``CustomDomainError`` and leaves no partial user/tenant behind, rather
    than failing deep inside the transaction after the expensive work (inline
    template parsing, password hashing) is already done.
    """

    validate_tenant_subdomain(subdomain)

    seeds = sum(
        [
            template is not None,
            html is not None,
            new_template is not None,
        ]
    )
    if seeds != 1:
        raise AccountSeedError(
            "Provide exactly one of template, html, or new_template."
        )

    html = (html or "").strip() or None
    if html is None and template is None and new_template is None:
        raise AccountSeedError(
            "Provide exactly one of template, html, or new_template."
        )

    if html is not None:
        # Same inline path the dashboard uses; name/slug derived from the site.
        new_template = {
            "name": (name or "").strip() or "Site template",
            "html_source": html,
        }

    if owner_user is not None:
        if owner_user.pk is None or username is not None or email is not None:
            raise ValueError("existing owner requires a saved user and no login fields")
        password = None
    else:
        password = generate_password()

    domain = custom_domains.normalize_domain(custom_domain) if custom_domain else ""
    if domain and not custom_domains.DOMAIN_RE.match(domain):
        raise CustomDomainError(
            "That doesn't look like a valid domain (e.g. training.acme.com)."
        )
    if domain and CustomDomain.objects.filter(domain=domain).exists():
        raise CustomDomainError(f"“{domain}” is already in use.")

    with transaction.atomic():
        inline = new_template is not None
        if inline:
            template = Template.objects.create(**new_template)

        if owner_user is None:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
            )
            user.is_active = True
            user.is_staff = False
            user.save(update_fields=["is_active", "is_staff"])
        else:
            user = User.objects.get(pk=owner_user.pk)

        tenant = Tenant.objects.create(
            name=name,
            subdomain=subdomain,
            custom_domain=custom_domain,
            template=template,
            owner=user,
            # Empty, not a copy of the defaults. merge_with_defaults() fills
            # every unedited field from the template at read time; a stored
            # copy would instead pin this site to the values the template held
            # on the day it was created, and keep overriding it forever.
            content={},
            is_published=is_published,
        )
        TenantMembership.objects.create(
            tenant=tenant,
            user=user,
            role=TenantMembership.ROLE_OWNER,
        )

        if domain:
            # Re-checked here (not just above) so a same-domain race between
            # the pre-check and this point still fails atomically instead of
            # tripping the DB's unique constraint mid-transaction.
            _custom_domain, error = custom_domains.add_custom_domain(tenant, domain)
            if error:
                raise CustomDomainError(error)

        if inline:
            template.tenant = tenant
            if template.has_editable_schema:
                template.editing_mode = Template.EDITING_EDITABLE
            template.save(update_fields=["tenant", "editing_mode", "updated_at"])
            if not template.versions.filter(number=1).exists():
                TemplateVersion.objects.create(
                    template=template,
                    number=1,
                    html_source=template.html_source,
                    schema=template.schema or {},
                    label="Initial",
                    saved_by=user,
                )
        else:
            # Library → clone into the new tenant; cross-tenant is refused.
            assign_template(tenant, template, user=user)
            tenant.refresh_from_db()

    return tenant, user, password


def create_scoped_login(tenant, *, username, email, role):
    """Create a non-staff user and membership scoped to ``tenant``.

    Returns ``(user, password, errors)``. On validation failure, ``user`` and
    ``password`` are ``None`` and ``errors`` contains user-facing messages.
    """
    username = (username or "").strip()
    email = (email or "").strip()
    if role not in dict(TenantMembership.ROLE_CHOICES):
        role = TenantMembership.ROLE_EDITOR

    errors = []
    if not username:
        errors.append("A username is required.")
    elif User.objects.filter(username__iexact=username).exists():
        errors.append(
            f"A user named “{username}” already exists. Pick a different username."
        )
    if errors:
        return None, None, errors

    password = generate_password()
    with transaction.atomic():
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
        )
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        user.save(update_fields=["is_active", "is_staff", "is_superuser"])
        TenantMembership.objects.create(tenant=tenant, user=user, role=role)
    return user, password, []
