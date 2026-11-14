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
