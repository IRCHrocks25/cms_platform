from functools import wraps

from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse


def tenant_member_required(view):
    """For views on tenant-scoped hosts. Requires ``request.tenant`` set
    and ``request.user`` to be a member of that tenant (or staff)."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.tenant is None:
            return HttpResponseForbidden(
                "This page is only available on a tenant site."
            )
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if not request.tenant.user_can_edit(request.user):
            return render(request, "dashboard/no_access.html", status=403)
        return view(request, *args, **kwargs)

    return wrapped


def agency_operator_required(view):
    """For the agency's multi-tenant dashboard. Requires staff/superuser
    and no tenant on the host."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.tenant is not None:
            return redirect("dashboard:tenant_home")
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if not (request.user.is_staff or request.user.is_superuser):
            return HttpResponseForbidden("Agency operator access required.")
        return view(request, *args, **kwargs)

    return wrapped
