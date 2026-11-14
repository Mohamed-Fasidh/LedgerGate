"""Validated tenant-scoped tools used by the agent and MCP adapter."""
from sqlalchemy.orm import Session
from app.models import Invoice, PurchaseOrder

def get_invoice(db: Session, tenant_id: str, invoice_id: int):
    obj = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id).first()
    if not obj:
        raise ValueError("Invoice not found")
    return {"id": obj.id, "invoice_number": obj.invoice_number, "amount": float(obj.amount), "currency": obj.currency}

def get_purchase_order(db: Session, tenant_id: str, po_id: int):
    obj = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id, PurchaseOrder.tenant_id == tenant_id).first()
    if not obj:
        raise ValueError("Purchase order not found")
    return {"id": obj.id, "po_number": obj.po_number, "total_amount": float(obj.total_amount), "currency": obj.currency}
