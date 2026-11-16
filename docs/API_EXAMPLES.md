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
