from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, Any

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=72)
    tenant_name: str = Field(min_length=2, max_length=100)

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    tax_id: Optional[str] = None

class POIn(BaseModel):
    po_number: str = Field(min_length=2, max_length=100)
    supplier_id: int = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    total_amount: float = Field(gt=0)
    description: Optional[str] = None

class InvoiceIn(BaseModel):
    invoice_number: str = Field(min_length=2, max_length=100)
    supplier_id: Optional[int] = None
    purchase_order_id: Optional[int] = None
    currency: str = Field(default="INR", min_length=3, max_length=3)
    amount: float = Field(gt=0)
    invoice_date: Optional[str] = None

class ExtractionConfirmIn(BaseModel):
    invoice_number: str = Field(min_length=2, max_length=100)
    amount: float = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    invoice_date: Optional[str] = None
    supplier_id: Optional[int] = None
    purchase_order_id: Optional[int] = None

class PolicyIn(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    body: str = Field(min_length=10, max_length=30000)

class ReconcileOut(BaseModel):
    invoice_id: int
    status: str
    decision: str
    findings: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    requires_human_review: bool
