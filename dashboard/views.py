import json
import re
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.text import slugify
from django.views.decorators.http import require_POST, require_GET

from core.models import (
    Template, Tenant, TenantMembership, MediaAsset, ContentVersion,
)
from core.permissions import agency_operator_required, tenant_member_required
from core.renderer import render_site, merge_with_defaults
from core.urls_helpers import build_tenant_url_bundle


User = get_user_model()


# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #


STARTER_TEMPLATE_HTML = """\
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>
  <div data-edit="hero.body" data-type="richtext" data-label="Body">
    <p>Tell visitors what you do.</p>
  </div>
  <img data-edit="hero.image" data-type="image" data-label="Photo"
       src="https://placehold.co/800x400" alt="">
  <a data-edit="hero.cta" data-type="link" data-label="Button" href="#">Learn more</a>
</section>

<style data-tokens>
  :root {
    --primary: #2563eb;
    --bg: #ffffff;
  }
</style>
"""


SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
PASSWORD_ALPHABET = (
    "abcdefghjkmnpqrstuvwxyz"
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "23456789"
)
SESSION_CREDS_KEY = "agency_one_time_creds"
CREDS_TTL_MINUTES = 10


# --------------------------------------------------------------------------- #
# Dispatcher                                                                   #
# --------------------------------------------------------------------------- #


def dashboard_root(request):
    """`/dashboard/` — branches on whether the host resolved to a tenant."""
    if request.tenant is not None:
        return tenant_home(request)
    return agency_home(request)


# --------------------------------------------------------------------------- #
# Agency: home / overview                                                      #
# --------------------------------------------------------------------------- #


@agency_operator_required
def agency_home(request):
    sites_qs = Tenant.objects.all()
    total_sites = sites_qs.count()
    published_sites = sites_qs.filter(is_published=True).count()
    draft_sites = total_sites - published_sites

    total_clients = (
        TenantMembership.objects.values("user_id").distinct().count()
    )
    total_templates = Template.objects.count()

    seven_days_ago = timezone.now() - timedelta(days=7)
    sites_edited_recently = (
        Tenant.objects
        .filter(versions__saved_at__gte=seven_days_ago)
        .distinct()
        .count()
    )

    recent_activity = (
        ContentVersion.objects
        .select_related("tenant", "saved_by")
        .order_by("-saved_at")[:10]
    )

    return render(
        request,
        "dashboard/home.html",
        {
            "stats": {
                "total_sites": total_sites,
                "published_sites": published_sites,
                "draft_sites": draft_sites,
                "total_clients": total_clients,
                "total_templates": total_templates,
                "sites_edited_recently": sites_edited_recently,
            },
            "recent_activity": recent_activity,
            "nav_section": "home",
        },
    )


# --------------------------------------------------------------------------- #
# Agency: templates                                                            #
# --------------------------------------------------------------------------- #


@agency_operator_required
def template_list(request):
    templates = (
        Template.objects.all()
        .annotate(tenant_count=Count("tenants"))
        .order_by("-updated_at")
    )
    return render(
        request,
        "dashboard/template_list.html",
        {"templates": templates, "nav_section": "templates"},
    )


@agency_operator_required
def template_create(request):
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        description = (request.POST.get("description") or "").strip()
        html_source = request.POST.get("html_source") or ""

        if not name or not html_source.strip():
            messages.error(request, "Name and HTML source are required.")
            return render(
                request,
                "dashboard/template_form.html",
                {"form_data": request.POST},
            )

        template = Template.objects.create(
            name=name,
            description=description,
            html_source=html_source,
        )
        messages.success(request, f"Template “{template.name}” created.")
        return redirect("dashboard:template_detail", pk=template.pk)

    return render(
        request,
        "dashboard/template_form.html",
        {
            "form_data": {
                "name": "",
                "description": "",
                "html_source": STARTER_TEMPLATE_HTML,
            },
        },
    )


@agency_operator_required
def template_detail(request, pk):
    template = get_object_or_404(Template, pk=pk)

    if request.method == "POST":
        template.name = (request.POST.get("name") or template.name).strip()
        template.description = (request.POST.get("description") or "").strip()
        template.html_source = request.POST.get("html_source") or template.html_source
        template.save()
        messages.success(request, "Template updated.")
        return redirect("dashboard:template_detail", pk=template.pk)

    return render(
        request,
        "dashboard/template_form.html",
        {
            "template": template,
            "form_data": {"name": "", "description": "", "html_source": ""},
        },
    )


@agency_operator_required
@require_POST
def template_delete(request, pk):
    template = get_object_or_404(Template, pk=pk)
    if template.tenants.exists():
        messages.error(
            request,
            "Can't delete a template while sites are using it. "
            "Switch those sites to a different template first.",
        )
        return redirect("dashboard:template_detail", pk=template.pk)
    template.delete()
    messages.success(request, "Template deleted.")
    return redirect("dashboard:template_list")


# --------------------------------------------------------------------------- #
# Agency: sites list                                                           #
# --------------------------------------------------------------------------- #


@agency_operator_required
def tenant_list(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "all").lower()

    tenants = (
        Tenant.objects.all()
        .select_related("template")
        .annotate(
            member_count=Count("memberships", distinct=True),
            last_edited=Max("versions__saved_at"),
        )
        .order_by("-updated_at")
    )

    if q:
        tenants = tenants.filter(Q(name__icontains=q) | Q(subdomain__icontains=q))
    if status == "published":
        tenants = tenants.filter(is_published=True)
    elif status == "draft":
        tenants = tenants.filter(is_published=False)

    return render(
        request,
        "dashboard/tenant_list.html",
        {
            "tenants": tenants,
            "q": q,
            "status": status,
            "nav_section": "sites",
        },
    )


# --------------------------------------------------------------------------- #
# Agency: new client flow                                                      #
# --------------------------------------------------------------------------- #


@agency_operator_required
def tenant_create(request):
    """One-screen new client flow: User + Tenant + Membership atomically."""
    templates = Template.objects.all().order_by("name")

    form_data = {
        "name": "",
        "subdomain": "",
        "template": "",
        "custom_domain": "",
        "client_username": "",
        "client_email": "",
    }

    if request.method != "POST":
        return render(
            request,
            "dashboard/tenant_form.html",
            {
                "templates": templates,
                "form_data": form_data,
                "nav_section": "sites",
            },
        )

    name = (request.POST.get("name") or "").strip()
    submitted_subdomain = (request.POST.get("subdomain") or "").strip().lower()
    subdomain = submitted_subdomain
    template_id = request.POST.get("template") or ""
    custom_domain = (request.POST.get("custom_domain") or "").strip()
    client_username = (request.POST.get("client_username") or "").strip()
    client_email = (request.POST.get("client_email") or "").strip()

    if not subdomain and name:
        subdomain = _generate_unique_subdomain_from_name(name)

    posted = {
        "name": name,
        "subdomain": submitted_subdomain,
        "template": template_id,
        "custom_domain": custom_domain,
        "client_username": client_username,
        "client_email": client_email,
    }

    errors = []

    if not name:
        errors.append("Site name is required.")

    sub_reason = _validate_subdomain(subdomain) if subdomain else None
    if sub_reason:
        errors.append({
            "invalid": "Subdomain must use lowercase letters, digits, and dashes only.",
            "reserved": f"“{subdomain}” is a reserved subdomain. Pick another.",
            "taken": f"“{subdomain}” is already taken. Pick another.",
        }[sub_reason])

    if not template_id:
        errors.append("Pick a template.")
    template = None
    if template_id:
        try:
            template = Template.objects.get(pk=template_id)
        except (Template.DoesNotExist, ValueError):
            errors.append("That template no longer exists.")

    if not client_username:
        errors.append("Client username is required.")
    elif User.objects.filter(username__iexact=client_username).exists():
        errors.append(
            f"A user named “{client_username}” already exists. "
            "Pick a different username, or add the existing user from the site detail page."
        )

    if errors:
        for e in errors:
            messages.error(request, e)
        return render(
            request,
            "dashboard/tenant_form.html",
            {
                "templates": templates,
                "form_data": posted,
                "nav_section": "sites",
            },
            status=400,
        )

    password = _generate_password()

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                username=client_username,
                email=client_email,
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
                is_published=True,
            )
            TenantMembership.objects.create(
                tenant=tenant,
                user=user,
                role=TenantMembership.ROLE_OWNER,
            )
    except Exception as exc:
        messages.error(
            request,
            f"Couldn't create the site: {exc}. Nothing was saved.",
        )
        return render(
            request,
            "dashboard/tenant_form.html",
            {
                "templates": templates,
                "form_data": posted,
                "nav_section": "sites",
            },
            status=400,
        )

    token = _stash_credentials_in_session(request, user, password)
    return redirect(
        f"{reverse('dashboard:site_created', args=[tenant.pk])}?token={token}"
    )


@agency_operator_required
@require_GET
def check_subdomain(request):
    """JSON endpoint: GET /dashboard/sites/check-subdomain/?value=..."""
    value = (request.GET.get("value") or "").strip()
    if not value:
        return JsonResponse({"available": False, "reason": "invalid"})
    reason = _validate_subdomain(value)
    if reason:
        return JsonResponse({"available": False, "reason": reason})
    return JsonResponse({"available": True})


def _validate_subdomain(value):
    """Return None if available, otherwise a reason code."""
    if not value or not SUBDOMAIN_RE.match(value):
        return "invalid"
    reserved = set(getattr(settings, "TENANT_RESERVED_SUBDOMAINS", set()))
    if value in reserved:
        return "reserved"
    if Tenant.objects.filter(subdomain=value).exists():
        return "taken"
    return None


def _generate_unique_subdomain_from_name(name):
    """
    Build a valid, available subdomain from a site name.

    Starts with slugified name and appends numeric suffixes on collisions.
    """
    max_len = 63
    base = slugify(name or "").strip("-")
    if not base:
        base = "site"
    base = base[:max_len].rstrip("-") or "site"

    candidate = base
    suffix = 1
    while _validate_subdomain(candidate) is not None:
        token = f"-{suffix}"
        stem = base[: max_len - len(token)].rstrip("-") or "site"
        candidate = f"{stem}{token}"
        suffix += 1
    return candidate


def _generate_password():
    return get_random_string(length=16, allowed_chars=PASSWORD_ALPHABET)


# --------------------------------------------------------------------------- #
# Agency: credentials (one-time view)                                          #
# --------------------------------------------------------------------------- #


def _stash_credentials_in_session(request, user, password):
    token = secrets.token_urlsafe(24)
    bucket = request.session.get(SESSION_CREDS_KEY) or {}
    bucket[token] = {
        "user_id": user.pk,
        "username": user.username,
        "password": password,
        "expires_at": (
            timezone.now() + timedelta(minutes=CREDS_TTL_MINUTES)
        ).isoformat(),
    }
    request.session[SESSION_CREDS_KEY] = bucket
    request.session.modified = True
    return token


def _pop_credentials_from_session(request, token):
    """Return the credentials dict and remove it. Returns None if missing/expired."""
    bucket = request.session.get(SESSION_CREDS_KEY) or {}
    payload = bucket.pop(token, None)
    if payload is None:
        return None
    request.session[SESSION_CREDS_KEY] = bucket
    request.session.modified = True
    try:
        expires_at = datetime.fromisoformat(payload["expires_at"])
    except (KeyError, ValueError):
        return None
    if timezone.now() > expires_at:
        return None
    return payload


@agency_operator_required
@require_GET
def site_created(request, pk):
    """
    Post-create / post-reveal landing for a tenant site.

    Merges the one-time credentials reveal with the shareable URL bundle
    so the operator gets everything in one shot. Without a fresh `?token=`,
    the credentials block is omitted and only the URL panel renders — the
    page is then safe to bookmark/refresh.
    """
    tenant = get_object_or_404(Tenant, pk=pk)
    token = request.GET.get("token") or ""
    payload = _pop_credentials_from_session(request, token) if token else None
    urls = build_tenant_url_bundle(request, tenant)

    return render(
        request,
        "dashboard/site_created.html",
        {
            "tenant": tenant,
            "payload": payload,
            "urls": urls,
            "agency_editor_url": reverse("dashboard:tenant_editor", args=[tenant.pk]),
            "tenant_detail_url": reverse("dashboard:tenant_detail", args=[tenant.pk]),
            "back_url": reverse("dashboard:tenant_list"),
            "user_detail_url": (
                reverse("dashboard:user_detail", args=[payload["user_id"]])
                if payload else None
            ),
            "nav_section": "sites",
        },
    )


# Back-compat alias: /sites/<pk>/credentials/ now lands on the same page.
site_credentials = site_created


@agency_operator_required
@require_GET
def user_credentials(request, pk):
    user = get_object_or_404(User, pk=pk)
    token = request.GET.get("token") or ""
    payload = _pop_credentials_from_session(request, token) if token else None

    return render(
        request,
        "dashboard/credentials.html",
        {
            "context_label": "user",
            "credentials_user": user,
            "payload": payload,
            "login_url": None,
            "back_url": reverse("dashboard:user_detail", args=[user.pk]),
            "back_label": "Done — back to user",
            "user_detail_url": reverse("dashboard:user_detail", args=[user.pk]),
        },
    )


# --------------------------------------------------------------------------- #
# Agency: site detail / membership management                                  #
# --------------------------------------------------------------------------- #


@agency_operator_required
def tenant_detail(request, pk):
    tenant = get_object_or_404(
        Tenant.objects.select_related("template", "owner"), pk=pk
    )
    members = (
        tenant.memberships
        .select_related("user")
        .order_by("user__username")
    )
    member_user_ids = list(members.values_list("user_id", flat=True))
    add_member_candidates = (
        User.objects.exclude(pk__in=member_user_ids)
        .filter(is_active=True)
        .order_by("username")[:200]
    )
    activity = (
        tenant.versions.select_related("saved_by").order_by("-saved_at")[:20]
    )
    return render(
        request,
        "dashboard/tenant_detail.html",
        {
            "tenant": tenant,
            "members": members,
            "add_member_candidates": add_member_candidates,
            "activity": activity,
            "nav_section": "sites",
            "role_choices": TenantMembership.ROLE_CHOICES,
        },
    )


@agency_operator_required
@require_POST
def tenant_settings_update(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    name = (request.POST.get("name") or "").strip()
    new_subdomain = (request.POST.get("subdomain") or "").strip().lower()
    custom_domain = (request.POST.get("custom_domain") or "").strip()

    if not name:
        messages.error(request, "Site name is required.")
        return redirect("dashboard:tenant_detail", pk=tenant.pk)

    if new_subdomain != tenant.subdomain:
        reason = _validate_subdomain(new_subdomain)
        if reason == "taken":
            # Check it's not just our own row.
            other = Tenant.objects.filter(subdomain=new_subdomain).exclude(pk=tenant.pk).exists()
            if other:
                messages.error(request, f"“{new_subdomain}” is already taken.")
                return redirect("dashboard:tenant_detail", pk=tenant.pk)
        elif reason:
            messages.error(
                request,
                {
                    "invalid": "Subdomain must use lowercase letters, digits, and dashes.",
                    "reserved": f"“{new_subdomain}” is a reserved subdomain.",
                }[reason],
            )
            return redirect("dashboard:tenant_detail", pk=tenant.pk)
        tenant.subdomain = new_subdomain

    tenant.name = name
    tenant.custom_domain = custom_domain
    tenant.save(update_fields=["name", "subdomain", "custom_domain", "updated_at"])
    messages.success(request, "Site settings updated.")
    return redirect("dashboard:tenant_detail", pk=tenant.pk)


@agency_operator_required
@require_POST
def tenant_delete(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    confirm = (request.POST.get("confirm_subdomain") or "").strip().lower()
    if confirm != tenant.subdomain.lower():
        messages.error(
            request,
            "To delete, you must type the site's subdomain exactly.",
        )
        return redirect("dashboard:tenant_detail", pk=tenant.pk)
    site_name = tenant.name
    tenant.delete()
    messages.success(request, f"Site “{site_name}” deleted.")
    return redirect("dashboard:tenant_list")


@agency_operator_required
@require_POST
def tenant_member_add(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    user_id = request.POST.get("user_id") or ""
    role = request.POST.get("role") or TenantMembership.ROLE_EDITOR
    if role not in dict(TenantMembership.ROLE_CHOICES):
        role = TenantMembership.ROLE_EDITOR
    try:
        user = User.objects.get(pk=user_id)
    except (User.DoesNotExist, ValueError):
        messages.error(request, "That user no longer exists.")
        return redirect("dashboard:tenant_detail", pk=tenant.pk)
    obj, created = TenantMembership.objects.get_or_create(
        tenant=tenant, user=user, defaults={"role": role}
    )
    if created:
        messages.success(request, f"Added {user.username} as {obj.get_role_display()}.")
    else:
        messages.info(request, f"{user.username} is already a member.")
    return redirect("dashboard:tenant_detail", pk=tenant.pk)


@agency_operator_required
@require_POST
def tenant_member_remove(request, pk, membership_id):
    tenant = get_object_or_404(Tenant, pk=pk)
    membership = get_object_or_404(
        TenantMembership, pk=membership_id, tenant=tenant
    )
    username = membership.user.username
    membership.delete()
    messages.success(request, f"Removed {username} from this site.")
    return redirect("dashboard:tenant_detail", pk=tenant.pk)


@agency_operator_required
@require_POST
def tenant_member_role(request, pk, membership_id):
    tenant = get_object_or_404(Tenant, pk=pk)
    membership = get_object_or_404(
        TenantMembership, pk=membership_id, tenant=tenant
    )
    role = request.POST.get("role") or membership.role
    if role not in dict(TenantMembership.ROLE_CHOICES):
        messages.error(request, "Unknown role.")
        return redirect("dashboard:tenant_detail", pk=tenant.pk)
    membership.role = role
    membership.save(update_fields=["role"])
    messages.success(request, f"Updated role for {membership.user.username}.")
    return redirect("dashboard:tenant_detail", pk=tenant.pk)


# --------------------------------------------------------------------------- #
# Agency: user management                                                      #
# --------------------------------------------------------------------------- #


@agency_operator_required
def user_list(request):
    q = (request.GET.get("q") or "").strip()
    role = (request.GET.get("role") or "all").lower()

    users = (
        User.objects.all()
        .annotate(membership_count=Count("tenant_memberships", distinct=True))
        .order_by("username")
    )
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q))
    if role == "staff":
        users = users.filter(Q(is_staff=True) | Q(is_superuser=True))
    elif role == "client":
        users = users.filter(is_staff=False, is_superuser=False)

    user_rows = []
    for u in users:
        memberships = list(
            TenantMembership.objects.select_related("tenant")
            .filter(user=u)
            .order_by("tenant__name")
        )
        site_names = [m.tenant.name for m in memberships]
        user_rows.append({
            "user": u,
            "site_names": site_names,
            "site_names_truncated": site_names[:3],
            "site_names_overflow": max(0, len(site_names) - 3),
            "membership_count": len(memberships),
        })

    return render(
        request,
        "dashboard/user_list.html",
        {
            "user_rows": user_rows,
            "q": q,
            "role": role,
            "nav_section": "users",
        },
    )


@agency_operator_required
def user_detail(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    memberships = (
        TenantMembership.objects.filter(user=user_obj)
        .select_related("tenant")
        .order_by("tenant__name")
    )
    return render(
        request,
        "dashboard/user_detail.html",
        {
            "user_obj": user_obj,
            "memberships": memberships,
            "nav_section": "users",
        },
    )


@agency_operator_required
@require_POST
def user_reset_password(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    password = _generate_password()
    user_obj.set_password(password)
    user_obj.save(update_fields=["password"])
    token = _stash_credentials_in_session(request, user_obj, password)
    return redirect(
        f"{reverse('dashboard:user_credentials', args=[user_obj.pk])}?token={token}"
    )


@agency_operator_required
@require_POST
def user_deactivate(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    if user_obj.pk == request.user.pk:
        messages.error(request, "You can't deactivate your own account.")
        return redirect("dashboard:user_detail", pk=user_obj.pk)
    user_obj.is_active = False
    user_obj.save(update_fields=["is_active"])
    messages.success(request, f"Deactivated {user_obj.username}.")
    return redirect("dashboard:user_detail", pk=user_obj.pk)


@agency_operator_required
@require_POST
def user_activate(request, pk):
    user_obj = get_object_or_404(User, pk=pk)
    user_obj.is_active = True
    user_obj.save(update_fields=["is_active"])
    messages.success(request, f"Activated {user_obj.username}.")
    return redirect("dashboard:user_detail", pk=user_obj.pk)


@agency_operator_required
@require_POST
def user_make_staff(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Only a superuser can promote agency staff.")
    user_obj = get_object_or_404(User, pk=pk)
    user_obj.is_staff = True
    user_obj.save(update_fields=["is_staff"])
    messages.success(request, f"{user_obj.username} is now agency staff.")
    return redirect("dashboard:user_detail", pk=user_obj.pk)


@agency_operator_required
@require_POST
def user_remove_membership(request, pk, membership_id):
    user_obj = get_object_or_404(User, pk=pk)
    membership = get_object_or_404(
        TenantMembership, pk=membership_id, user=user_obj
    )
    tenant_name = membership.tenant.name
    membership.delete()
    messages.success(
        request, f"Removed {user_obj.username} from “{tenant_name}”."
    )
    return redirect("dashboard:user_detail", pk=user_obj.pk)


# --------------------------------------------------------------------------- #
# Agency-side editor (unchanged from previous spec)                            #
# --------------------------------------------------------------------------- #


@agency_operator_required
def tenant_editor(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _render_editor(request, tenant, scope="agency")


@agency_operator_required
def tenant_preview(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _render_preview(tenant)


@agency_operator_required
@require_POST
def tenant_save(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _save_content(request, tenant)


@agency_operator_required
@require_POST
def tenant_publish(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _toggle_publish(request, tenant, redirect_name="dashboard:tenant_editor")


@agency_operator_required
@require_POST
def tenant_upload(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _save_upload(request, tenant)


# --------------------------------------------------------------------------- #
# Tenant surface (tenant resolved on host, member or staff only)               #
# --------------------------------------------------------------------------- #


@tenant_member_required
def tenant_home(request):
    return _render_editor(request, request.tenant, scope="tenant")


@tenant_member_required
def tenant_preview_self(request):
    return _render_preview(request.tenant)


@tenant_member_required
@require_POST
def tenant_save_self(request):
    return _save_content(request, request.tenant)


@tenant_member_required
@require_POST
def tenant_publish_self(request):
    return _toggle_publish(request, request.tenant, redirect_name="dashboard:tenant_home")


@tenant_member_required
@require_POST
def tenant_upload_self(request):
    return _save_upload(request, request.tenant)


# --------------------------------------------------------------------------- #
# Shared helpers                                                               #
# --------------------------------------------------------------------------- #


def _render_editor(request, tenant, *, scope):
    schema = tenant.template.schema or {"sections": []}
    content = merge_with_defaults(schema, tenant.content)

    sections = schema.get("sections", [])
    grouped: dict[str, list] = {}
    for section in sections:
        grouped.setdefault(section.get("group", "Sections"), []).append(section)

    layout_mode = "compact" if len(sections) <= 6 else (
        "standard" if len(sections) <= 15 else "dense"
    )

    if scope == "tenant":
        preview_url = reverse("dashboard:tenant_preview_self")
        save_url = reverse("dashboard:tenant_save_self")
        upload_url = reverse("dashboard:tenant_upload_self")
        publish_url = reverse("dashboard:tenant_publish_self")
    else:
        preview_url = reverse("dashboard:tenant_preview", args=[tenant.pk])
        save_url = reverse("dashboard:tenant_save", args=[tenant.pk])
        upload_url = reverse("dashboard:tenant_upload", args=[tenant.pk])
        publish_url = reverse("dashboard:tenant_publish", args=[tenant.pk])

    return render(
        request,
        "dashboard/editor.html",
        {
            "tenant": tenant,
            "schema": schema,
            "sections": sections,
            "grouped_sections": grouped,
            "content_json": json.dumps(content),
            "layout_mode": layout_mode,
            "preview_url": preview_url,
            "save_url": save_url,
            "upload_url": upload_url,
            "publish_url": publish_url,
            "scope": scope,
        },
    )


def _render_preview(tenant):
    content = merge_with_defaults(tenant.template.schema, tenant.content)
    html = render_site(tenant.template.html_source, content, preview=True)
    return HttpResponse(html)


def _save_content(request, tenant):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    content = payload.get("content")
    if not isinstance(content, dict):
        return HttpResponseBadRequest("content must be an object")

    ContentVersion.objects.create(
        tenant=tenant,
        snapshot=tenant.content,
        saved_by=request.user,
    )
    keep_ids = list(
        tenant.versions.values_list("id", flat=True).order_by("-saved_at")[:10]
    )
    tenant.versions.exclude(id__in=keep_ids).delete()

    tenant.content = content
    tenant.save(update_fields=["content", "updated_at"])

    return JsonResponse({"ok": True, "updated_at": tenant.updated_at.isoformat()})


def _toggle_publish(request, tenant, *, redirect_name):
    tenant.is_published = not tenant.is_published
    tenant.save(update_fields=["is_published", "updated_at"])
    state = "published" if tenant.is_published else "unpublished"
    messages.success(request, f"Site {state}.")
    if redirect_name == "dashboard:tenant_home":
        return redirect(redirect_name)
    return redirect(redirect_name, pk=tenant.pk)


def _save_upload(request, tenant):
    upload = request.FILES.get("file")
    if not upload:
        return HttpResponseBadRequest("No file")

    asset = MediaAsset.objects.create(
        tenant=tenant,
        file=upload,
        original_name=upload.name,
    )
    return JsonResponse({"ok": True, "url": asset.file.url, "id": asset.id})
