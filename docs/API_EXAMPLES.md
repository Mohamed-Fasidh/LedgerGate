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



## Tenant-wide idempotency

The `Idempotency-Key` namespace is shared by invoice creation, invoice uploads, and the other supported state-changing API operations within each tenant. A key already reserved by a different operation or request returns HTTP 409. Invoice creation and upload retain compatibility with historical invoice records while also reserving new keys in the shared `idempotency_records` table. Migration `0012_unified_invoice_idempotency` backfills historical invoice keys; it deliberately stops with an explicit error if a historical key collides with a key already used by another operation, rather than silently changing either record.

## Idempotency-key retention

Idempotency responses are retained for 90 days by default. A tenant administrator can remove older records in bounded batches; cleanup is scoped to the authenticated tenant by both the query and PostgreSQL row-level security. The operation is audited. Use the same access token and repeat while `has_more` is true:

```http
POST /api/v1/maintenance/idempotency/cleanup?retention_days=90&batch_size=1000
Authorization: Bearer <tenant_admin_access_token>
```

The retention window must be between 30 and 3,650 days, and each batch is limited to 10,000 records. The endpoint only deletes idempotency response records; it does not delete invoices, supplier records, audit events, or uploaded files. After the retention window expires and records are cleaned up, an old key may be accepted as a new request, so clients should not retry business operations indefinitely. This endpoint is not a scheduler: run it periodically from an authenticated tenant maintenance process.

## Refresh-token lifecycle

Authentication responses return a short-lived access token and set a 30-day refresh credential in a host-only `HttpOnly` cookie (`ledgergate_refresh`). The cookie is scoped to `/api/v1/auth`, uses `SameSite=Lax`, and is marked `Secure` in production. The API also sets a separate readable `ledgergate_csrf` cookie. Browser clients must send its value in the `X-CSRF-Token` header for refresh and logout requests; the API also validates the request `Origin` against `CORS_ORIGINS`. Do not copy the refresh cookie into JavaScript storage. Successful refresh rotates the cookie automatically. Reuse of a previously rotated refresh token revokes the entire token family. `POST /api/v1/auth/logout` revokes the current refresh session and clears both cookies. Access tokens remain valid until their short expiry; logout does not immediately revoke already-issued access tokens. Use HTTPS in deployed environments. Since the CSRF cookie is host-only and read by the browser client, serve the frontend and API on the same hostname (a reverse proxy for `/api` is recommended); configure that exact frontend origin in `CORS_ORIGINS`.


### Log out all devices

Use the authenticated access token to revoke every unexpired refresh-token session for the current user in the current tenant. The endpoint also clears the current browser's refresh and CSRF cookies:

```http
POST /api/v1/auth/logout-all
Authorization: Bearer <access_token>
```

The endpoint returns the number of refresh sessions revoked. Existing access tokens remain valid until their short expiry; use short access-token lifetimes and refresh-token revocation together.


## Expired refresh-session retention

A tenant administrator can remove expired refresh-session rows after an investigation grace period. The default grace period is 30 days after token expiry; the endpoint never selects unexpired sessions, and deletion is tenant-scoped and audit logged:

```http
POST /api/v1/maintenance/auth-sessions/cleanup?retention_days=30&batch_size=1000
Authorization: Bearer <access_token>
```

`retention_days` is the number of days to retain a session after its `expires_at` timestamp (0–3,650). Batches are limited to 10,000 rows; repeat while `has_more` is true. Run this endpoint periodically from an authenticated tenant maintenance process. It is not a scheduler.


## Authenticator-app MFA

Users can enroll a TOTP authenticator after signing in. The setup response returns the shared secret and an `otpauth://` URI once; display it only to the account owner and never log it. The secret is encrypted before being stored. Enabling MFA also returns ten one-time recovery codes; store them securely because only their hashes are retained and the raw codes are shown once.

1. Start enrollment with the current password:

```http
POST /api/v1/auth/mfa/setup
Content-Type: application/x-www-form-urlencoded

current_password=<current-password>
```

2. Add the returned secret to an authenticator app, then confirm a current six-digit code:

```http
POST /api/v1/auth/mfa/enable
Content-Type: application/x-www-form-urlencoded

totp_code=<six-digit-code>
```

3. Include `totp_code` with the password at `POST /api/v1/auth/token` whenever MFA is enabled. A one-time `recovery_code` can be used instead if the authenticator is unavailable. To disable MFA, send `current_password` and either a fresh `totp_code` or an unused `recovery_code` to `POST /api/v1/auth/mfa/disable`. The authenticated status endpoint is `GET /api/v1/auth/mfa/status`.

TOTP uses 30-second SHA-1 time steps, accepts a one-step clock-skew window, and prevents reuse of a consumed time step. Failed MFA attempts count toward the existing login throttling. Store `MFA_ENCRYPTION_KEY` as a secret; generate it with `Fernet.generate_key()`. Losing or rotating this key without decrypt-and-reencrypt migration makes existing MFA secrets unreadable.
