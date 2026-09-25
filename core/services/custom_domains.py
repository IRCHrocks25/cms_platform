"""Custom-domain add + verify logic shared by the agency dashboard and the
MCP tools (CMS-32). Keeping normalisation, validation, and the verification
threshold in one place means the two surfaces can never drift.
"""
from __future__ import annotations

import re

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdataclass
import dns.rdatatype
import dns.resolver
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import CustomDomain, Tenant


DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
)


def normalize_domain(raw_domain: str) -> str:
    return (raw_domain or "").strip().lower().rstrip(".")


# Per-exchange and whole-lookup timeouts (seconds). The dashboard verify is a
# synchronous request, so a lame nameserver must not hang it.
_NS_QUERY_TIMEOUT = 3.0
_NS_LOOKUP_LIFETIME = 5.0
_MAX_CNAME_DEPTH = 8


def _nameserver_ips(domain: str) -> list:
    """IPv4 addresses of the authoritative nameservers for ``domain``'s zone.

    The zone cut and NS set come from the recursive resolver: those records
    change rarely, and a stale NS set still points at servers that answer
    for the zone. Raises ``dns.exception.DNSException`` on failure.
    """
    resolver = dns.resolver.Resolver()
    resolver.lifetime = _NS_LOOKUP_LIFETIME
    zone = dns.resolver.zone_for_name(domain, resolver=resolver)
    ips = []
    for ns in resolver.resolve(zone, "NS"):
        try:
            ips.extend(r.address for r in resolver.resolve(ns.target, "A"))
        except dns.exception.DNSException:
            continue
    return sorted(set(ips))


def _query_nameserver(ns_ip: str, qname: str) -> dns.message.Message:
    """One A query sent straight to ``ns_ip``, retried over TCP if truncated."""
    query = dns.message.make_query(qname, "A")
    response = dns.query.udp(query, ns_ip, timeout=_NS_QUERY_TIMEOUT)
    if response.flags & dns.flags.TC:
        response = dns.query.tcp(query, ns_ip, timeout=_NS_QUERY_TIMEOUT)
    return response


def _answer_rrset(response, name, rdtype):
    for rrset in response.answer:
        if (
            rrset.name == name
            and rrset.rdclass == dns.rdataclass.IN
            and rrset.rdtype == rdtype
        ):
            return rrset
    return None


def _read_answer(response: dns.message.Message, qname: str):
    """Walk the answer section from ``qname`` through any CNAMEs it carries.

    Returns ``(addresses, None)`` when the chain ends in A records (or in
    nothing, e.g. NXDOMAIN), or ``(set(), target)`` when it ends in a CNAME
    whose target this server didn't include (an out-of-zone alias).
    """
    name = dns.name.from_text(qname)
    for _ in range(_MAX_CNAME_DEPTH):
        a_rrset = _answer_rrset(response, name, dns.rdatatype.A)
        if a_rrset is not None:
            return {r.address for r in a_rrset}, None
        cname = _answer_rrset(response, name, dns.rdatatype.CNAME)
        if cname is None:
            break
        name = cname[0].target
    if name != dns.name.from_text(qname):
        return set(), name.to_text(omit_final_dot=True)
    return set(), None


def _authoritative_a_records(domain: str, seen: frozenset) -> set:
    if domain in seen or len(seen) >= _MAX_CNAME_DEPTH:
        return set()
    seen = seen | {domain}
    addresses, chase = set(), set()
    for ns_ip in _nameserver_ips(domain):
        try:
            response = _query_nameserver(ns_ip, domain)
        except (dns.exception.DNSException, OSError):
            continue  # an unreachable NS doesn't veto the ones that answer
        if response.rcode() not in (dns.rcode.NOERROR, dns.rcode.NXDOMAIN):
            continue
        found, target = _read_answer(response, domain)
        addresses |= found
        if target:
            chase.add(target)
    for target in chase:
        addresses |= _authoritative_a_records(target, seen)
    return addresses


def resolve_a_records(domain: str) -> list:
    """A records for ``domain`` as its authoritative nameservers serve them.

    Deliberately bypasses the container's caching resolver (CMS-65): it held
    a replaced record for the old record's full TTL, long after the zone
    changed. Every nameserver is asked and the union returned, so a zone
    that is still propagating shows both the new and the old address, which
    is also what Let's Encrypt may see. Empty list on any failure.
    """
    try:
        return sorted(_authoritative_a_records(domain, frozenset()))
    except (dns.exception.DNSException, OSError):
        return []


def add_custom_domain(tenant: Tenant, raw_domain: str):
    """Normalise, validate, and create a ``CustomDomain`` row for ``tenant``.

    Returns ``(custom_domain, error)``; exactly one is set. No external
    registration step: the client just points an A record at our origin. The
    row starts unverified; ``verify_custom_domain`` confirms the DNS resolves
    to us before the route-syncer emits the Traefik router (which is what
    triggers Let's Encrypt issuance).
    """
    domain = normalize_domain(raw_domain)
    if not domain:
        return None, "Enter a domain to add."
    if not DOMAIN_RE.match(domain):
        return None, "That doesn't look like a valid domain (e.g. training.acme.com)."
    if CustomDomain.objects.filter(domain=domain).exists():
        return None, f"“{domain}” is already in use."

    custom_domain = CustomDomain.objects.create(
        tenant=tenant, domain=domain, is_verified=False
    )
    sync_tenant_primary_domain(tenant)
    return custom_domain, None


def verify_custom_domain(custom_domain: CustomDomain):
    """Resolve ``custom_domain``'s A records and flip ``is_verified`` when they
    are exactly ``settings.CUSTOM_DOMAIN_TARGET_IP``. A stale address next to
    ours fails: Let's Encrypt may validate against it. Returns
    ``(is_verified, resolved)``. ``resolved`` is always returned (even on
    success) so callers needing the raw addresses don't have to re-resolve.
    """
    target_ip = settings.CUSTOM_DOMAIN_TARGET_IP
    resolved = resolve_a_records(custom_domain.domain)

    if resolved == [target_ip]:
        if not custom_domain.is_verified:
            custom_domain.is_verified = True
            custom_domain.verified_at = timezone.now()
            custom_domain.save(
                update_fields=["is_verified", "verified_at", "updated_at"]
            )
        # Sync on every successful check, not only on the flip: re-verifying
        # an already-verified domain is how an operator repairs a drifted
        # Tenant.custom_domain from the dashboard (CMS-63 review fix).
        sync_tenant_primary_domain(custom_domain.tenant)
        return True, resolved

    return False, resolved


def delete_custom_domain(custom_domain: CustomDomain) -> None:
    """Delete ``custom_domain`` and re-sync the owning tenant's display hint.

    Dropping the row removes the host from the next route-syncer pass, so
    Traefik stops serving it; no external cleanup is needed here.
    """
    tenant = custom_domain.tenant
    custom_domain.delete()
    sync_tenant_primary_domain(tenant)


def sync_tenant_primary_domain(tenant: Tenant) -> bool:
    """Keep the legacy ``Tenant.custom_domain`` display hint in step with the
    CustomDomain table: the earliest verified domain, or ``""`` when none.

    Routing and the URL helpers (``core.urls_helpers``) key off the rows;
    this field is only a display hint, but it must not drift after any
    add/verify/delete, whichever surface performed it (CMS-63). Returns
    ``True`` when the stored value changed and refreshes ``tenant`` so the
    caller's instance matches the DB. Idempotent: an in-step tenant costs
    reads only, no write.
    """
    with transaction.atomic():
        # Compare against the persisted value under a row lock, never the
        # caller's in-memory copy: a second instance of the same tenant with
        # a stale custom_domain would otherwise skip a needed write.
        locked = (
            Tenant.objects.select_for_update()
            .only("pk", "custom_domain")
            .get(pk=tenant.pk)
        )
        primary = (
            locked.custom_domains.filter(is_verified=True)
            .order_by("created_at", "pk")
            .values_list("domain", flat=True)
            .first()
            or ""
        )
        changed = locked.custom_domain != primary
        if changed:
            locked.custom_domain = primary
            locked.save(update_fields=["custom_domain", "updated_at"])
    tenant.custom_domain = primary
    return changed
