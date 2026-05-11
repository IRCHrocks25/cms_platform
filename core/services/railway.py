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


def add_custom_domain(domain: str) -> bool:
    """Register a custom domain with Railway for the CMS service.
    Returns True on success (no GraphQL errors in the response)."""
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
        body = resp.json()
    except ValueError:
        body = resp.text
    logger.info(
        "Railway add_custom_domain response (status=%s): %s", resp.status_code, body
    )
    resp.raise_for_status()
    return "errors" not in body


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
