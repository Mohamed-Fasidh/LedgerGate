from types import SimpleNamespace

import pytest

from app.financial_controls import ensure_independent_decision


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_invoice_creator_cannot_make_own_decision(decision):
    invoice = SimpleNamespace(created_by_user_id=12)
    with pytest.raises(ValueError, match="cannot"):
        ensure_independent_decision(invoice, 12, decision)


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_different_reviewer_can_decide_invoice(decision):
    invoice = SimpleNamespace(created_by_user_id=12)
    ensure_independent_decision(invoice, 13, decision)


def test_legacy_invoice_without_creator_remains_decidable():
    invoice = SimpleNamespace(created_by_user_id=None)
    ensure_independent_decision(invoice, 12, "approve")


@pytest.mark.parametrize(
    ("amount", "currency", "threshold", "expected"),
    [
        (99999.99, "INR", 100000, 1),
        (100000, "INR", 100000, 2),
        (250000, "inr", 100000, 2),
        (500000, "USD", 100000, 1),
        (None, "INR", 100000, 1),
    ],
)
def test_high_value_invoice_approval_threshold(amount, currency, threshold, expected):
    from app.financial_controls import required_invoice_approvals

    assert required_invoice_approvals(amount, currency, threshold) == expected


@pytest.mark.parametrize("status", ["approved", "rejected", "extraction_review"])
def test_final_or_unconfirmed_invoice_cannot_be_reconciled(status):
    from app.financial_controls import ensure_reviewable_status
    from app.models import InvoiceStatus
    invoice = SimpleNamespace(status=InvoiceStatus(status))
    with pytest.raises(ValueError, match="Only received"):
        ensure_reviewable_status(invoice)


def test_received_invoice_can_be_reconciled():
    from app.financial_controls import ensure_reviewable_status
    from app.models import InvoiceStatus
    ensure_reviewable_status(SimpleNamespace(status=InvoiceStatus.received))


@pytest.mark.parametrize("status", ["received", "review_required", "approved", "rejected"])
def test_extraction_confirmation_is_one_time(status):
    from app.financial_controls import ensure_extraction_editable
    from app.models import InvoiceStatus
    with pytest.raises(ValueError, match="awaiting extraction review"):
        ensure_extraction_editable(SimpleNamespace(status=InvoiceStatus(status)))


def test_extraction_review_can_be_confirmed():
    from app.financial_controls import ensure_extraction_editable
    from app.models import InvoiceStatus
    ensure_extraction_editable(SimpleNamespace(status=InvoiceStatus.extraction_review))


def test_foreign_currency_policy_snapshot_is_single_approval():
    from app.financial_controls import required_invoice_approvals
    assert required_invoice_approvals(500000, "USD", 100000) == 1


def test_policy_threshold_is_applied_at_version_boundary():
    from app.financial_controls import required_invoice_approvals
    assert required_invoice_approvals(150000, "INR", 100000) == 2
    assert required_invoice_approvals(150000, "INR", 200000) == 1
