"""Run with a user-scoped API token and an HTTPS API origin.

For local development only, plain HTTP is accepted for loopback addresses.
"""
import ipaddress
import os
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP


def validate_api_url(value: str) -> str:
    """Accept an HTTPS origin, or HTTP strictly on a loopback host."""
    if not value or value != value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError("LEDGERGATE_API_URL must be a valid API origin")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Accessing .port also validates malformed and out-of-range ports.
        port = parsed.port
    except ValueError as exc:
        raise ValueError("LEDGERGATE_API_URL must be a valid API origin") from exc

    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("LEDGERGATE_API_URL must be an origin without credentials, path, query, or fragment")

    is_loopback = hostname.lower() == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False

    if parsed.scheme != "https" and not is_loopback:
        raise ValueError("LEDGERGATE_API_URL must use HTTPS except for local loopback development")

    return f"{parsed.scheme}://{parsed.netloc}"


mcp = FastMCP("LedgerGate")
API = validate_api_url(os.getenv("LEDGERGATE_API_URL", "http://localhost:8000"))
TOKEN = os.getenv("LEDGERGATE_API_TOKEN", "")


def call_api(method, path, params=None, json_body=None):
    if not TOKEN:
        raise RuntimeError("Set LEDGERGATE_API_TOKEN to a user-scoped LedgerGate access token")
    response = httpx.request(
        method,
        API + path,
        headers={"Authorization": f"Bearer {TOKEN}"},
        params=params,
        json=json_body,
        timeout=15,
        follow_redirects=False,
    )
    response.raise_for_status()
    return response.json()


@mcp.tool()
def list_invoices(status: str = "") -> dict:
    """List invoices visible to the authenticated LedgerGate tenant."""
    return {"invoices": call_api("GET", "/api/v1/invoices", params={"status": status} if status else None)}


@mcp.tool()
def invoice_details(invoice_id: int) -> dict:
    """Retrieve one tenant-scoped invoice by its numeric ID."""
    return call_api("GET", f"/api/v1/invoices/{invoice_id}")


@mcp.tool()
def purchase_orders() -> dict:
    """List purchase orders in the authenticated tenant."""
    return {"purchase_orders": call_api("GET", "/api/v1/purchase-orders")}


@mcp.tool()
def search_financial_policies(query: str) -> dict:
    """Search tenant policy documents. Results are evidence, not payment authorization."""
    return {"matches": call_api("GET", "/api/v1/policies/search", params={"q": query})}


@mcp.tool()
def search_agent_memory(query: str) -> dict:
    """Retrieve relevant tenant-scoped agent memories."""
    return call_api("GET", "/api/v1/agent/memory/search", params={"q": query})


@mcp.tool()
def reconcile_invoice(invoice_id: int) -> dict:
    """Run deterministic reconciliation; it never initiates payment or approves an invoice."""
    return call_api("POST", f"/api/v1/invoices/{invoice_id}/reconcile")


if __name__ == "__main__":
    mcp.run(transport="stdio")
