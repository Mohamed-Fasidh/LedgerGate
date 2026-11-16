# API examples

Register:
```bash
curl -X POST http://localhost:8000/api/v1/auth/register -H 'Content-Type: application/json' -d '{"email":"admin@example.com","password":"change-this-password-123","tenant_name":"Acme"}'
```
Use the returned token:
```bash
TOKEN='paste-token'
curl -X POST http://localhost:8000/api/v1/suppliers -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"name":"Acme Supplies","tax_id":"GSTIN-EXAMPLE"}'
```
Create a PO (replace supplier_id):
```bash
curl -X POST http://localhost:8000/api/v1/purchase-orders -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"po_number":"PO-1001","supplier_id":1,"currency":"INR","total_amount":50000}'
```
Create an invoice (replace IDs):
```bash
curl -X POST http://localhost:8000/api/v1/invoices -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"invoice_number":"INV-1001","supplier_id":1,"purchase_order_id":1,"currency":"INR","amount":65000}'
```
Reconcile:
```bash
curl -X POST http://localhost:8000/api/v1/invoices/1/reconcile -H "Authorization: Bearer $TOKEN"
```

Upload an invoice with an idempotency key (retry with the same key, filename, and file bytes if the response is lost):
```bash
curl -X POST http://localhost:8000/api/v1/invoices/upload \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Idempotency-Key: upload-invoice-2026-0001' \
  -F 'file=@./invoice.pdf'
```
The upload key is scoped to the tenant and shared with invoice-creation keys. Repeating the same upload request returns the original invoice without creating another stored file. Reusing the key for a different file or a different invoice operation returns HTTP 409. Keys must contain 8-128 printable ASCII characters.

Create an amount-based approval policy as an administrator:
```bash
curl -X POST http://localhost:8000/api/v1/approval-policies \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"High value invoices","min_amount":100000,"max_amount":null,"approval_steps":[["reviewer"],["admin"]]}'
```
The example requires a reviewer approval followed by a different administrator approval for invoices of INR 100,000 or more. Amount ranges use an exclusive maximum; active policies for a tenant cannot overlap. The selected policy is snapshotted on the invoice when its first decision is submitted, so later policy changes do not rewrite an in-flight approval.

Submit an approval at the current stage (the authenticated user must have a role allowed by that stage and must not be the invoice creator):
```bash
curl -X POST http://localhost:8000/api/v1/invoices/1/approve \
  -H "Authorization: Bearer $REVIEWER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"comment":"Matched against the purchase order."}'
```
A successful response either advances the invoice to the next stage or marks it approved after the final stage. Inspect the recorded decisions:
```bash
curl http://localhost:8000/api/v1/invoices/1/approvals \
  -H "Authorization: Bearer $TOKEN"
```


## Refresh-token lifecycle

Authentication responses return a short-lived access token and set a 30-day refresh credential in a host-only `HttpOnly` cookie (`ledgergate_refresh`). The cookie is scoped to `/api/v1/auth`, uses `SameSite=Lax`, and is marked `Secure` in production. The API also sets a separate readable `ledgergate_csrf` cookie. Browser clients must send its value in the `X-CSRF-Token` header for refresh and logout requests; the API also validates the request `Origin` against `CORS_ORIGINS`. Do not copy the refresh cookie into JavaScript storage. Successful refresh rotates the cookie automatically. Reuse of a previously rotated refresh token revokes the entire token family. `POST /api/v1/auth/logout` revokes the current refresh session and clears both cookies. Access tokens remain valid until their short expiry; logout does not immediately revoke already-issued access tokens. Use HTTPS in deployed environments. Since the CSRF cookie is host-only and read by the browser client, serve the frontend and API on the same hostname (a reverse proxy for `/api` is recommended); configure that exact frontend origin in `CORS_ORIGINS`.


### Log out all devices

Use the authenticated access token to revoke every unexpired refresh-token session for the current user in the current tenant. The endpoint also clears the current browser's refresh and CSRF cookies:

```http
POST /api/v1/auth/logout-all
Authorization: Bearer <access_token>
```

The endpoint returns the number of refresh sessions revoked. Existing access tokens remain valid until their short expiry; use short access-token lifetimes and refresh-token revocation together.
