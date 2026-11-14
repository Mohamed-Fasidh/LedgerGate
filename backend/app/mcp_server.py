"""Run with: LEDGERGATE_API_TOKEN=... LEDGERGATE_API_URL=http://localhost:8000 python -m app.mcp_server"""
import os
import httpx
from mcp.server.fastmcp import FastMCP

mcp=FastMCP("LedgerGate")
API=os.getenv("LEDGERGATE_API_URL","http://localhost:8000").rstrip("/")
TOKEN=os.getenv("LEDGERGATE_API_TOKEN","")

def call_api(method,path,params=None,json_body=None):
    if not TOKEN:
        raise RuntimeError("Set LEDGERGATE_API_TOKEN to a user-scoped LedgerGate access token")
    response=httpx.request(method,API+path,headers={"Authorization":f"Bearer {TOKEN}"},params=params,json=json_body,timeout=15)
    response.raise_for_status()
    return response.json()

@mcp.tool()
def list_invoices(status: str = "") -> dict:
    """List invoices visible to the authenticated LedgerGate tenant."""
    return {"invoices":call_api("GET","/api/v1/invoices",params={"status":status} if status else None)}

@mcp.tool()
def invoice_details(invoice_id: int) -> dict:
    """Retrieve one tenant-scoped invoice by its numeric ID."""
    return call_api("GET",f"/api/v1/invoices/{invoice_id}")

@mcp.tool()
def purchase_orders() -> dict:
    """List purchase orders in the authenticated tenant."""
    return {"purchase_orders":call_api("GET","/api/v1/purchase-orders")}

@mcp.tool()
def search_financial_policies(query: str) -> dict:
    """Search tenant policy documents. Results are evidence, not payment authorization."""
    return {"matches":call_api("GET","/api/v1/policies/search",params={"q":query})}

@mcp.tool()
def search_agent_memory(query: str) -> dict:
    """Retrieve relevant tenant-scoped agent memories."""
    return call_api("GET","/api/v1/agent/memory/search",params={"q":query})

@mcp.tool()
def reconcile_invoice(invoice_id: int) -> dict:
    """Run deterministic reconciliation; it never initiates payment or approves an invoice."""
    return call_api("POST",f"/api/v1/invoices/{invoice_id}/reconcile")

if __name__=="__main__":
    mcp.run(transport="stdio")
