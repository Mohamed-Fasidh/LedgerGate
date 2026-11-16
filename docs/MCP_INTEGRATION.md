# MCP server integration

LedgerGate includes a local stdio Model Context Protocol (MCP) server in `backend/app/mcp_server.py`. It calls the same authenticated FastAPI endpoints as the dashboard and does not connect directly to PostgreSQL or object storage.

## Security model

- Configure a **user-scoped LedgerGate access token** in `LEDGERGATE_API_TOKEN`. Never use a database password, refresh token, JWT signing key, or administrator-wide service credential as the MCP token.
- Access tokens are short-lived (30 minutes by default; production configuration permits at most 60 minutes). The MCP adapter does not store or rotate refresh tokens. When the access token expires, authenticate again and restart the MCP process with the new access token.
- Set `LEDGERGATE_API_URL` to the API origin only, without a path, query, or fragment.
- Remote API origins must use HTTPS. Plain HTTP is accepted only for loopback development addresses (`localhost`, `127.0.0.1`, or `::1`). TLS certificate verification remains enabled by the HTTP client.
- Authenticated requests do not follow redirects. Configure the correct final API origin instead of relying on redirects.
- Keep the access token in the MCP host's protected environment/secret configuration. Do not commit it to the repository, shell history, logs, or a shared MCP configuration file.
- MCP tool annotations are user-interface hints, not authorization. FastAPI authentication, tenant isolation, and role checks remain authoritative.

## Local setup

From the repository root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:LEDGERGATE_API_URL = "http://localhost:8000"
$env:LEDGERGATE_API_TOKEN = "<short-lived-user-access-token>"
python -m app.mcp_server
```

For macOS/Linux, activate the environment with `source .venv/bin/activate` and export the two environment variables before running `python -m app.mcp_server`.

The default URL is `http://localhost:8000` for local development. For a remote deployment, set `LEDGERGATE_API_URL` to the public HTTPS API origin, for example `https://ledgergate.example.com`.

The process uses stdio transport and is intended to be launched by an MCP host as a local child process. It is not an independently exposed HTTP MCP endpoint.

## Available tools and side effects

| Tool | Behavior | Required API authorization |
|---|---|---|
| `list_invoices` | Read tenant invoices | Authenticated user |
| `invoice_details` | Read one tenant invoice | Authenticated user |
| `purchase_orders` | Read tenant purchase orders | Authenticated user |
| `search_financial_policies` | Search tenant policy documents | Authenticated user |
| `search_agent_memory` | Search tenant-scoped agent memories | Authenticated user |
| `reconcile_invoice` | **Writes reconciliation output and an audit event**; not idempotent | Admin or analyst |

The reconciliation tool does not approve or reject an invoice, release funds, or initiate a payment. Human approval remains in the authorized approval workflow. Reconciliation can replace the stored reconciliation result, so MCP clients should treat it as a write and request user confirmation where supported.

## Operational checks

1. Verify the MCP process receives a valid, unexpired access token for the intended user and tenant.
2. For remote API access, verify the HTTPS hostname and certificate chain from the MCP host.
3. Confirm the user has the intended role; reconciliation requires admin or analyst.
4. Confirm invoice and purchase-order results remain scoped to the authenticated tenant.
5. Rotate the access token after suspected exposure and revoke the associated session through LedgerGate's session controls.
6. Do not treat MCP annotations or a successful local connection as a substitute for API authorization or production network controls.
