"""Custom-domain add + verify logic shared by the agency dashboard and the
MCP tools (CMS-32). Keeping normalisation, validation, and the verification
threshold in one place means the two surfaces can never drift.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait

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
from core.services import acme_health


DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
)


def normalize_domain(raw_domain: str) -> str:
    return (raw_domain or "").strip().lower().rstrip(".")


# One deadline (seconds) for the whole lookup, zone discovery and CNAME chase
# included. The dashboard verify is a synchronous request, so several lame
# nameservers must not add up. Per-exchange timeouts are capped by it too.
_LOOKUP_DEADLINE = 8.0
_NS_QUERY_TIMEOUT = 3.0
_MAX_CNAME_DEPTH = 8


class DnsAnswer(list):
    """Sorted A-record addresses, plus ``problems``: authorities that answered
    but not cleanly (NXDOMAIN, SERVFAIL/REFUSED, no A record), or a lookup
    that ran out of time. It is a list so callers comparing addresses keep
    working; verification also requires ``problems`` to be empty, because
    Let's Encrypt may ask exactly the authority that failed. An authority that
    doesn't respond at all is not a problem while another one answers: LE's
    resolver moves on from a timeout too.
    """

    def __init__(self, addresses=(), problems=()):
        super().__init__(sorted(addresses))
        self.problems = tuple(problems)


class _DeadlineExceeded(Exception):
    pass


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise _DeadlineExceeded()
    return left


def _nameserver_ips(domain: str, deadline: float) -> list:
    """IPv4 addresses of the authoritative nameservers for ``domain``'s zone.

    The zone cut and NS set come from the recursive resolver: those records
    change rarely, and a stale NS set still points at servers that answer
    for the zone. Every step draws on the same absolute ``deadline``. Raises
    ``dns.exception.DNSException`` or ``_DeadlineExceeded`` on failure.
    """
    resolver = dns.resolver.Resolver()
    zone = dns.resolver.zone_for_name(
        domain, resolver=resolver, lifetime=_remaining(deadline)
    )
    ns_names = [
        ns.target
        for ns in resolver.resolve(zone, "NS", lifetime=_remaining(deadline))
    ]
    ips = []
    for answer in _parallel(
        lambda n: resolver.resolve(n, "A", lifetime=_remaining(deadline)),
        ns_names,
        deadline,
    ):
        if not isinstance(answer, Exception):
            ips.extend(r.address for r in answer)
    return sorted(set(ips))


def _query_nameserver(
    ns_ip: str, qname: str, timeout: float = _NS_QUERY_TIMEOUT
) -> dns.message.Message:
    """One A query sent straight to ``ns_ip``, retried over TCP if truncated."""
    query = dns.message.make_query(qname, "A")
    response = dns.query.udp(query, ns_ip, timeout=timeout)
    if response.flags & dns.flags.TC:
        response = dns.query.tcp(query, ns_ip, timeout=timeout)
    return response


# One small pool for every lookup in the process. A timed-out exchange can't
# be cancelled once running, so a per-request pool would leave its threads
# behind; a shared, capped pool bounds that to _MAX_DNS_WORKERS threads, each
# freed within one per-exchange timeout.
_MAX_DNS_WORKERS = 8
_EXECUTOR = ThreadPoolExecutor(
    max_workers=_MAX_DNS_WORKERS, thread_name_prefix="dns-verify"
)


def _parallel(fn, items, deadline: float) -> list:
    """Run ``fn`` over ``items`` on the shared pool; each result is the return
    value or the exception raised. Raises ``_DeadlineExceeded`` if any is
    still unfinished at ``deadline`` (queued ones are cancelled, running ones
    finish on their own bounded timeouts)."""
    if not items:
        return []
    futures = [_EXECUTOR.submit(fn, item) for item in items]
    _done, pending = futures_wait(futures, timeout=_remaining(deadline))
    if pending:
        for future in pending:
            future.cancel()
        raise _DeadlineExceeded()
    results = []
    for future in futures:
        try:
            results.append(future.result())
        except Exception as exc:  # noqa: BLE001; returned as data
            results.append(exc)
    return results


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
    nothing), or ``(set(), target)`` when it ends in a CNAME whose target this
    server didn't include (an out-of-zone alias).
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


def _authoritative_a_records(domain: str, seen: frozenset, deadline: float):
    """``(addresses, problems)`` across every authority for ``domain``."""
    if domain in seen or len(seen) >= _MAX_CNAME_DEPTH:
        return set(), []
    seen = seen | {domain}
    nameservers = _nameserver_ips(domain, deadline=deadline)
    per_query = min(_NS_QUERY_TIMEOUT, _remaining(deadline))
    responses = _parallel(
        lambda ns_ip: _query_nameserver(ns_ip, domain, timeout=per_query),
        nameservers,
        deadline,
    )
    addresses, problems, chase = set(), [], set()
    for ns_ip, response in zip(nameservers, responses):
        if isinstance(response, Exception):
            continue  # silent authority: see DnsAnswer
        rcode = response.rcode()
        if rcode != dns.rcode.NOERROR:
            problems.append(f"{ns_ip}: {dns.rcode.to_text(rcode)}")
            continue
        found, target = _read_answer(response, domain)
        if target:
            chase.add(target)
        elif not found:
            problems.append(f"{ns_ip}: no A record")
        addresses |= found
    for target in sorted(chase):
        found, target_problems = _authoritative_a_records(target, seen, deadline)
        addresses |= found
        problems.extend(target_problems)
    return addresses, problems


def resolve_a_records(domain: str) -> DnsAnswer:
    """A records for ``domain`` as its authoritative nameservers serve them.

    Deliberately bypasses the container's caching resolver (CMS-65): it held
    a replaced record for the old record's full TTL, long after the zone
    changed. Every nameserver is asked (concurrently, under one deadline) and
    the union returned, so a zone that is still propagating shows both the
    new and the old address, which is also what Let's Encrypt may see.
    Failures come back as an empty answer, never an exception.
    """
    deadline = time.monotonic() + _LOOKUP_DEADLINE
    try:
        addresses, problems = _authoritative_a_records(domain, frozenset(), deadline)
    except _DeadlineExceeded:
        return DnsAnswer(problems=("DNS lookup timed out",))
    except (dns.exception.DNSException, OSError):
        return DnsAnswer()
    return DnsAnswer(addresses, problems)


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


def mark_verified(custom_domain: CustomDomain) -> None:
    """Flip ``is_verified`` and stamp ``verified_at``. Every path that verifies
    a domain (DNS check or superuser force-verify) goes through here, so the
    ACME health check, which keys off ``verified_at``, sees all of them."""
    custom_domain.is_verified = True
    custom_domain.verified_at = timezone.now()
    custom_domain.save(update_fields=["is_verified", "verified_at", "updated_at"])


def verify_custom_domain(custom_domain: CustomDomain):
    """Resolve ``custom_domain``'s A records and flip ``is_verified`` when they
    are exactly ``settings.CUSTOM_DOMAIN_TARGET_IP``. A stale address next to
    ours fails: Let's Encrypt may validate against it. Returns
    ``(is_verified, resolved)``. ``resolved`` is always returned (even on
    success) so callers needing the raw addresses don't have to re-resolve.
    """
    target_ip = settings.CUSTOM_DOMAIN_TARGET_IP
    resolved = resolve_a_records(custom_domain.domain)

    if resolved == [target_ip] and not getattr(resolved, "problems", ()):
        if custom_domain.is_verified:
            if custom_domain.verified_at is None:
                mark_verified(custom_domain)
            # Re-verify is how an operator reports "still no certificate":
            # force a new ACME order instead of doing nothing (CMS-65).
            acme_health.reverify_certificate(custom_domain)
        else:
            mark_verified(custom_domain)
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
