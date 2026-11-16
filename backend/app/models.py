import enum
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Numeric, Text, Boolean, Enum, JSON, Integer, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

def now_utc():
    return datetime.now(timezone.utc)

class Role(str, enum.Enum):
    admin = "admin"
    reviewer = "reviewer"
    analyst = "analyst"

class InvoiceStatus(str, enum.Enum):
    received = "received"
    extraction_review = "extraction_review"
    processing = "processing"
    review_required = "review_required"
    approved = "approved"
    rejected = "rejected"

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_name: Mapped[str] = mapped_column(String(150), default="Workspace")
    email: Mapped[str] = mapped_column(String(255), index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.analyst)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    auth_token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_last_used_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mfa_recovery_code_hashes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        UniqueConstraint("email", name="uq_user_email"),
        Index("ix_users_tenant_created_id", "tenant_id", "created_at", "id"),
    )

class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    tax_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_tenant_supplier_name"),
        Index("ix_suppliers_tenant_id_id", "tenant_id", "id"),
    )

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    po_number: Mapped[str] = mapped_column(String(100))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    total_amount: Mapped[float] = mapped_column(Numeric(14,2))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        UniqueConstraint("tenant_id", "po_number", name="uq_tenant_po"),
        Index("ix_purchase_orders_tenant_id_id", "tenant_id", "id"),
    )

class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    invoice_number: Mapped[str] = mapped_column(String(100))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    amount: Mapped[float | None] = mapped_column(Numeric(14,2), nullable=True)
    invoice_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[InvoiceStatus] = mapped_column(Enum(InvoiceStatus), default=InvoiceStatus.received)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    reconciliation: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_operation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    idempotency_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_policy_id: Mapped[int | None] = mapped_column(ForeignKey("approval_policies.id"), nullable=True)
    approval_steps_snapshot: Mapped[list | None] = mapped_column(JSON, nullable=True)
    approval_stage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__ = (
        Index("ix_invoice_tenant_number_supplier", "tenant_id", "invoice_number", "supplier_id"),
        Index("ix_invoices_tenant_created_id", "tenant_id", "created_at", "id"),
        Index("ix_invoices_tenant_status_created_id", "tenant_id", "status", "created_at", "id"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_invoice_tenant_idempotency"),
    )
    __mapper_args__ = {"version_id_col": version}

class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class AgentMemory(Base):
    __tablename__ = "agent_memories"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    memory_type: Mapped[str] = mapped_column(String(40), default="episodic")
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    __table_args__ = (
        Index("ix_audit_events_tenant_created_id", "tenant_id", "created_at", "id"),
        Index("ix_audit_events_tenant_id_id", "tenant_id", "id"),
    )

class RegistrationAttempt(Base):
    """Privacy-preserving registration throttle events; raw email/IP are never stored."""
    __tablename__ = "registration_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    email_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, index=True)
    __table_args__ = (Index("ix_registration_attempts_email_time", "email_hash", "attempted_at"), Index("ix_registration_attempts_ip_time", "ip_hash", "attempted_at"))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    email_hash: Mapped[str] = mapped_column(String(64), index=True)
    ip_hash: Mapped[str] = mapped_column(String(64), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    __table_args__ = (
        Index("ix_login_attempts_email_failed_time", "email_hash", "succeeded", "attempted_at"),
        Index("ix_login_attempts_ip_failed_time", "ip_hash", "succeeded", "attempted_at"),
    )

class ApprovalPolicy(Base):
    __tablename__ = "approval_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120))
    min_amount: Mapped[Decimal] = mapped_column(Numeric(14,2), default=Decimal('0.00'))
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(14,2), nullable=True)
    approval_steps: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_tenant_approval_policy_name"),
        Index("ix_approval_policies_tenant_min_id", "tenant_id", "min_amount", "id"),
    )

class InvoiceApproval(Base):
    __tablename__ = "invoice_approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("approval_policies.id"), nullable=True)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("tenant_id", "invoice_id", "approver_user_id", name="uq_invoice_approval_actor"),)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    family_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_token_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        Index("ix_auth_sessions_tenant_expires", "tenant_id", "expires_at"),
        Index("ix_auth_sessions_tenant_user_active_created", "tenant_id", "user_id", "revoked_at", "created_at", "id"),
        Index("ix_auth_sessions_tenant_family_active", "tenant_id", "family_id", "revoked_at"),
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_idempotency_tenant_key"),
        Index("ix_idempotency_tenant_created_id", "tenant_id", "created_at", "id"),
    )


class PasswordResetToken(Base):
    """Hashed, single-use password reset token; raw email and token are never stored."""
    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    __table_args__ = (Index("ix_password_reset_tokens_user_created", "user_id", "created_at"),)


class EmailVerificationToken(Base):
    """Hashed, single-use email verification token."""
    __tablename__ = "email_verification_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    __table_args__ = (Index("ix_email_verification_tokens_user_created", "user_id", "created_at"),)


class OutboundEmail(Base):
    """Encrypted, durable outbox record for account-security email delivery."""
    __tablename__ = "outbound_emails"
    id: Mapped[int] = mapped_column(primary_key=True)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    message_type: Mapped[str] = mapped_column(String(40), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=12, server_default="12")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
    __table_args__ = (
        Index("ix_outbound_emails_pending_due", "status", "next_attempt_at", "id"),
        Index("ix_outbound_emails_locked_at", "status", "locked_at"),
    )
