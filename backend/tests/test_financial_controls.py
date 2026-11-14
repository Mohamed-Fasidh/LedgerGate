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


def test_legacy_invoice_without_creator_remains_decidable(decision="approve"):
    invoice = SimpleNamespace(created_by_user_id=None)
    ensure_independent_decision(invoice, 12, decision)
