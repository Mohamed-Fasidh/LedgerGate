from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, Any

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=72)
    tenant_name: str = Field(min_length=2, max_length=100)

    @field_validator("password")
    @classmethod
    def password_must_fit_bcrypt_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 UTF-8 bytes")
        return value

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


class ApprovalPolicyIn(BaseModel):
    currency: str = Field(default="INR", min_length=3, max_length=3)
    threshold_amount: float = Field(gt=0, le=1000000000000)
    required_approvals: int = Field(default=2, ge=1, le=5)

    @field_validator("currency")
    @classmethod
    def only_supported_policy_currency(cls, value: str) -> str:
        normalized = value.upper()
        if normalized != "INR":
            raise ValueError("Only INR approval policies are supported until FX policy support is implemented")
        return normalized


class RefreshTokenIn(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)
