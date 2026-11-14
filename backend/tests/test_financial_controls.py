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
