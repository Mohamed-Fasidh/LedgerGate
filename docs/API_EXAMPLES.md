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

Authentication responses now include a short-lived access token and a 30-day refresh token. Clients should keep refresh tokens in protected storage, send them only to `POST /api/v1/auth/refresh`, and replace the stored refresh token on every successful rotation. Reuse of a previously rotated refresh token revokes the entire token family. `POST /api/v1/auth/logout` revokes the submitted refresh token. Access tokens remain valid until their short expiry; logout does not provide immediate access-token revocation. Use HTTPS and secure client-side storage in deployed environments.
