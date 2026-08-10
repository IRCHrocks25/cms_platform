from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.crypto import get_random_string

from core.models import CustomDomain, Template, TemplateVersion, Tenant, TenantMembership
from core.services import custom_domains
from core.services.templates import assign_template


User = get_user_model()

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


def generate_password():
    return get_random_string(length=16, allowed_chars=PASSWORD_ALPHABET)


def create_tenant_account(
    *,
    name,
    subdomain,
    custom_domain,
    username,
    email,
    template=None,
    html=None,
    new_template=None,
    is_published=True,
):
    """Create a tenant, owner login, and owner membership atomically.

    Seed the home template with exactly one of:

    - ``template`` — library (or already-resolved) Template to clone
    - ``html`` — raw HTML string; creates a tenant-owned template named after
      the site (CMS-38). Unannotated HTML correctly yields an empty schema /
      ``raw`` editing mode via ``Template.save``
    - ``new_template`` — kwargs for an inline Template (dashboard new-client
      path); richer than ``html`` when a custom name/description is needed

    The generated password is returned to the caller and is never persisted
    outside Django's password hash.

    ``is_published`` defaults to True for the dashboard new-client flow.
    MCP ``create_client_account`` passes False so chat-created sites stay draft.

    If ``custom_domain`` is supplied, a real (unverified) ``CustomDomain`` row
    is created for it via ``core.services.custom_domains.add_custom_domain`` —
    that's the table the middleware and route-syncer actually key off, so a
    domain passed here now does something (CMS-37). The domain is checked
    *before* any row is created so an invalid/taken domain raises
    ``CustomDomainError`` and leaves no partial user/tenant behind, rather
    than failing deep inside the transaction after the expensive work (inline
    template parsing, password hashing) is already done.
    """
    html = (html or "").strip() or None
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

    if html is not None:
        # Same inline path the dashboard uses; name/slug derived from the site.
        new_template = {
            "name": (name or "").strip() or "Site template",
            "html_source": html,
        }

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

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
        )
        user.is_active = True
        user.is_staff = False
        user.save(update_fields=["is_active", "is_staff"])

        tenant = Tenant.objects.create(
            name=name,
            subdomain=subdomain,
            custom_domain=custom_domain,
            template=template,
            owner=user,
            content=template.schema.get("defaults", {}) or {},
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
            # Defaults came from the library row; re-seed from the clone.
            tenant.content = tenant.template.schema.get("defaults", {}) or {}
            tenant.save(update_fields=["content", "updated_at"])

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
