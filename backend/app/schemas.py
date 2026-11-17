from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from typing import Optional, Any
from decimal import Decimal

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=72)
    tenant_name: str = Field(min_length=2, max_length=100)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 UTF-8 bytes")
        return value

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
    currency: str = Field(default="INR", min_length=3, max_length=3)
    min_amount: Decimal = Field(default=Decimal('0.00'), ge=0)
    max_amount: Optional[Decimal] = Field(default=None, gt=0)
    approval_steps: list[list[str]] = Field(min_length=1, max_length=5)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_policy_currency(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Currency must be a three-letter alphabetic code")
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("Currency must be a three-letter alphabetic code")
        return normalized

class ApprovalDecisionIn(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=1000)


class TenantUserRoleUpdateIn(BaseModel):
    role: str = Field(pattern="^(analyst|reviewer)$")


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=12, max_length=72)
    totp_code: Optional[str] = Field(default=None, min_length=6, max_length=6)
    recovery_code: Optional[str] = Field(default=None, min_length=24, max_length=32)

    @field_validator("current_password", "new_password")
    @classmethod
    def passwords_fit_bcrypt_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 UTF-8 bytes")
        return value


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    new_password: str = Field(min_length=12, max_length=72)
    totp_code: Optional[str] = Field(default=None, min_length=6, max_length=6)
    recovery_code: Optional[str] = Field(default=None, min_length=24, max_length=32)

    @field_validator("new_password")
    @classmethod
    def reset_password_fits_bcrypt_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 UTF-8 bytes")
        return value


class EmailVerificationRequestIn(BaseModel):
    email: EmailStr


class EmailVerificationConfirmIn(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class AgentTaskIn(BaseModel):
    """User task description; tenant scope always comes from the access token."""
    task: str = Field(min_length=5, max_length=4000)

    @field_validator("task")
    @classmethod
    def normalize_agent_task(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 5:
            raise ValueError("Task description must contain at least five non-whitespace characters")
        return normalized
