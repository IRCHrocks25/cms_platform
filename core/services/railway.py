import httpx
from django.conf import settings

RAILWAY_API = "https://backboard.railway.app/graphql/v2"


def _headers():
    return {
        "Authorization": f"Bearer {settings.RAILWAY_API_TOKEN}",
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
    resp = httpx.post(
        RAILWAY_API,
        headers=_headers(),
        json={
            "query": mutation,
            "variables": {
                "input": {
                    "domain": domain,
                    "serviceId": settings.RAILWAY_SERVICE_ID,
                    "environmentId": settings.RAILWAY_ENVIRONMENT_ID,
                }
            },
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return "errors" not in data


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
