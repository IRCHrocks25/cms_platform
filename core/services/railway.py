import logging

import httpx
from django.conf import settings

RAILWAY_API = "https://backboard.railway.app/graphql/v2"

logger = logging.getLogger(__name__)


def _headers():
    token = settings.RAILWAY_TOKEN
    if not token:
        raise RuntimeError(
            "RAILWAY_TOKEN is not set. Add it to the service's environment "
            "variables in the Railway dashboard (Service → Variables)."
        )
    if not settings.RAILWAY_SERVICE_ID or not settings.RAILWAY_ENVIRONMENT_ID:
        raise RuntimeError(
            "RAILWAY_SERVICE_ID and RAILWAY_ENVIRONMENT_ID must both be set "
            "in the service's environment variables."
        )
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def test_connection() -> dict:
    """Test token validity. Returns the raw GraphQL response."""
    query = "query { me { name email } }"
    resp = httpx.post(
        RAILWAY_API, headers=_headers(), json={"query": query}, timeout=10
    )
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    logger.info("Railway test_connection (status=%s): %s", resp.status_code, body)
    return body


def check_domain_availability(domain: str) -> dict:
    """Ask Railway whether a domain can be added. Per Railway's
    schema this query only takes ``domain``."""
    query = """
    query customDomainAvailable($domain: String!) {
        customDomainAvailable(domain: $domain) {
            available
            message
        }
    }
    """
    variables = {"domain": domain}
    logger.info("Railway check_domain_availability variables: %s", variables)
    resp = httpx.post(
        RAILWAY_API,
        headers=_headers(),
        json={"query": query, "variables": variables},
        timeout=10,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    logger.info(
        "Railway check_domain_availability (status=%s): %s", resp.status_code, body
    )
    return body


def introspect_custom_domain_input() -> dict:
    """Introspect Railway's ``CustomDomainCreateInput`` to find the real
    field names. Logs the full response."""
    query = """
    query {
        __type(name: "CustomDomainCreateInput") {
            name
            inputFields {
                name
                type {
                    name
                    kind
                    ofType {
                        name
                        kind
                    }
                }
            }
        }
    }
    """
    resp = httpx.post(
        RAILWAY_API,
        headers=_headers(),
        json={"query": query},
        timeout=10,
    )
    body = resp.json()
    logger.info(f"CustomDomainCreateInput schema: {body}")
    return body


def list_custom_domains() -> dict:
    """Return every custom domain currently registered on the service.
    Logs the full response so callers don't have to."""
    query = """
    query {
        service(id: "%s") {
            domains(environmentId: "%s") {
                customDomains {
                    id
                    domain
                }
            }
        }
    }
    """ % (settings.RAILWAY_SERVICE_ID, settings.RAILWAY_ENVIRONMENT_ID)
    resp = httpx.post(
        RAILWAY_API,
        headers=_headers(),
        json={"query": query},
        timeout=10,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    logger.info(
        "Railway list_custom_domains (status=%s): %s", resp.status_code, body
    )
    return body


def add_custom_domain(domain: str) -> bool:
    """Register a custom domain with Railway for the CMS service.
    Logs the full request + response. Returns True if Railway confirms
    a created hostname id, OR if the domain is already registered on
    the service."""
    # Short-circuit if Railway already has it — the create mutation
    # 400s on duplicates with an unhelpful "Problem processing request"
    # message, so we ask first.
    try:
        existing = list_custom_domains()
        registered = (
            ((existing.get("data") or {}).get("service") or {})
            .get("domains", {})
            .get("customDomains", [])
        ) or []
        if any((d or {}).get("domain") == domain for d in registered):
            logger.info(
                "Railway already has %s registered — skipping create.", domain
            )
            return True
    except Exception as e:
        logger.error(
            "Railway list_custom_domains failed during pre-check for %s: %s",
            domain, e, exc_info=True,
        )

    mutation = """
    mutation customDomainCreate($input: CustomDomainCreateInput!) {
        customDomainCreate(input: $input) {
            id
            domain
        }
    }
    """
    variables = {
        "input": {
            "domain": domain,
            "serviceId": settings.RAILWAY_SERVICE_ID,
            "environmentId": settings.RAILWAY_ENVIRONMENT_ID,
        }
    }
    logger.info("Railway add_custom_domain request variables: %s", variables)
    resp = httpx.post(
        RAILWAY_API,
        headers=_headers(),
        json={"query": mutation, "variables": variables},
        timeout=10,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}
    logger.info(
        "Railway add_custom_domain status=%s response=%s", resp.status_code, data
    )
    if resp.status_code != 200 or "errors" in data:
        logger.error("Railway domain add failed: %s", data)
        return False
    return bool(
        ((data.get("data") or {}).get("customDomainCreate") or {}).get("id")
    )


def remove_custom_domain(domain: str) -> bool:
    """Remove a custom domain from Railway. Returns True if the domain
    was deleted or was already absent."""
    query = """
    query getCustomDomain($serviceId: String!, $environmentId: String!) {
        service(id: $serviceId) {
            domains(environmentId: $environmentId) {
                customDomains {
                    id
                    domain
                }
            }
        }
    }
    """
    resp = httpx.post(
        RAILWAY_API,
        headers=_headers(),
        json={
            "query": query,
            "variables": {
                "serviceId": settings.RAILWAY_SERVICE_ID,
                "environmentId": settings.RAILWAY_ENVIRONMENT_ID,
            },
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    domains = (
        data.get("data", {})
        .get("service", {})
        .get("domains", {})
        .get("customDomains", [])
    )
    domain_id = next((d["id"] for d in domains if d["domain"] == domain), None)
    if not domain_id:
        return True  # already gone

    mutation = """
    mutation customDomainDelete($id: String!) {
        customDomainDelete(id: $id)
    }
    """
    resp = httpx.post(
        RAILWAY_API,
        headers=_headers(),
        json={"query": mutation, "variables": {"id": domain_id}},
        timeout=10,
    )
    resp.raise_for_status()
    return True
