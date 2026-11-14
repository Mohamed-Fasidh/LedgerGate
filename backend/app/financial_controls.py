"""Deterministic financial workflow controls."""


def ensure_independent_decision(invoice, actor_user_id: int, decision: str) -> None:
    """Reject a maker attempting to approve or reject their own invoice."""
    creator_id = getattr(invoice, "created_by_user_id", None)
    if creator_id is not None and creator_id == actor_user_id:
        raise ValueError(
            f"Maker-checker policy: the invoice creator cannot {decision} their own invoice"
        )


def required_invoice_approvals(amount, currency: str, threshold_inr: float) -> int:
    """Return two approvals for invoices at/above the INR threshold.

    Foreign-currency invoices retain the existing single-reviewer workflow
    until tenant-specific FX conversion and threshold policies are configured.
    """
    if (currency or "").upper() == "INR" and amount is not None and float(amount) >= threshold_inr:
        return 2
    return 1


def ensure_reviewable_status(invoice) -> None:
    """Only active, non-final invoices may be reconciled."""
    from app.models import InvoiceStatus
    if invoice.status not in (InvoiceStatus.received, InvoiceStatus.processing, InvoiceStatus.review_required):
        raise ValueError("Only received or in-review invoices can be reconciled")


def ensure_extraction_editable(invoice) -> None:
    """Extraction data is editable only in the initial human-confirmation state."""
    from app.models import InvoiceStatus
    if invoice.status != InvoiceStatus.extraction_review:
        raise ValueError("Invoice extraction can only be confirmed while awaiting extraction review")


def snapshot_approval_policy(db, invoice, tenant_id: str, actor_user_id: int, default_threshold: float):
    """Attach an invoice to the latest immutable tenant policy exactly once."""
    from app.models import ApprovalPolicy
    from sqlalchemy import text

    if invoice.required_approvals_snapshot is not None:
        return
    currency = (invoice.currency or "INR").upper()
    if currency != "INR":
        invoice.required_approvals_snapshot = 1
        invoice.approval_threshold_snapshot = None
        return
    policy = db.query(ApprovalPolicy).filter(
        ApprovalPolicy.tenant_id == tenant_id,
        ApprovalPolicy.currency == currency,
    ).order_by(ApprovalPolicy.version.desc()).first()
    if policy is None:
        policy = ApprovalPolicy(
            tenant_id=tenant_id, version=1, currency="INR",
            threshold_amount=default_threshold, required_approvals=2,
            created_by_user_id=actor_user_id,
        )
        db.add(policy)
        db.flush()
    invoice.approval_policy_id = policy.id
    invoice.approval_policy_version = policy.version
    invoice.approval_threshold_snapshot = policy.threshold_amount
    invoice.required_approvals_snapshot = (
        policy.required_approvals
        if invoice.amount is not None and float(invoice.amount) >= float(policy.threshold_amount)
        else 1
    )


def create_approval_policy_version(db, tenant_id: str, actor_user_id: int, currency: str, threshold_amount: float, required_approvals: int):
    """Create a new immutable policy version under a tenant-scoped transaction lock."""
    from app.models import ApprovalPolicy
    from sqlalchemy import text

    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {"lock_key": "approval-policy:" + tenant_id})
    latest = db.query(ApprovalPolicy).filter(
        ApprovalPolicy.tenant_id == tenant_id,
        ApprovalPolicy.currency == currency,
    ).order_by(ApprovalPolicy.version.desc()).first()
    policy = ApprovalPolicy(
        tenant_id=tenant_id,
        version=(latest.version + 1) if latest else 1,
        currency=currency,
        threshold_amount=threshold_amount,
        required_approvals=required_approvals,
        created_by_user_id=actor_user_id,
    )
    db.add(policy)
    db.flush()
    return policy
