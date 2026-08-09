from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.crypto import get_random_string

from core.models import Template, TemplateVersion, Tenant, TenantMembership
from core.services.templates import assign_template


User = get_user_model()

PASSWORD_ALPHABET = (
    "abcdefghjkmnpqrstuvwxyz"
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "23456789"
)


def generate_password():
    return get_random_string(length=16, allowed_chars=PASSWORD_ALPHABET)


def create_tenant_account(
    *,
    name,
    subdomain,
    custom_domain,
    template,
    username,
    email,
    new_template=None,
    is_published=True,
):
    """Create a tenant, owner login, and owner membership atomically.

    ``new_template`` may contain the keyword arguments for an inline Template
    creation. The generated password is returned to the caller and is never
    persisted outside Django's password hash.

    ``is_published`` defaults to True for the dashboard new-client flow.
    MCP ``create_client_account`` passes False so chat-created sites stay draft.
    """
    password = generate_password()

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
