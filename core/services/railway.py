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
    """Ask Railway whether a domain can be added to the service."""
    query = """
    query customDomainAvailable($domainName: String!, $serviceId: String!, $environmentId: String!) {
        customDomainAvailable(domainName: $domainName, serviceId: $serviceId, environmentId: $environmentId) {
            available
            message
        }
    }
    """
    variables = {
        "domainName": domain,
        "serviceId": settings.RAILWAY_SERVICE_ID,
        "environmentId": settings.RAILWAY_ENVIRONMENT_ID,
    }
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


def add_custom_domain(domain: str) -> bool:
    """Register a custom domain with Railway for the CMS service.
    Logs the full request + response. Returns True only when Railway
    confirms a created hostname id."""
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
