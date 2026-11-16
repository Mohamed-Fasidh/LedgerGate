from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, Any
from decimal import Decimal

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=72)
    tenant_name: str = Field(min_length=2, max_length=100)

class TokenOut(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"

class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=4096)

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

class ApprovalPolicyIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    min_amount: Decimal = Field(default=Decimal('0.00'), ge=0)
    max_amount: Optional[Decimal] = Field(default=None, gt=0)
    approval_steps: list[list[str]] = Field(min_length=1, max_length=5)

class ApprovalDecisionIn(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=1000)


class TenantUserRoleUpdateIn(BaseModel):
    role: str = Field(pattern="^(analyst|reviewer)$")


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=12, max_length=72)
    totp_code: Optional[str] = Field(default=None, min_length=6, max_length=6)
    recovery_code: Optional[str] = Field(default=None, min_length=24, max_length=32)
