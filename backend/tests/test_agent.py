from decimal import Decimal\nfrom types import SimpleNamespace\n\nfrom app.agent.runtime import AgentContext, ReconciliationAgent, build_reconciliation_context


def _context(invoice_amount, po_amount):
    return AgentContext(
        invoice={"invoice_number": "INV-1", "amount": invoice_amount, "currency": "INR"},
        purchase_order={"po_number": "PO-1", "total_amount": po_amount, "currency": "INR"},
    )


def test_amount_discrepancy_requires_human_review():
    result = ReconciliationAgent().run(_context("65000.00", "50000.00"))
    assert result.state.value == "human_review"
    assert any(item["code"] == "AMOUNT_EXCEEDS_PO" for item in result.findings)


def test_exact_match_completes():
    result = ReconciliationAgent().run(_context("50000.00", "50000.00"))
    assert result.state.value == "complete"
    assert result.findings == []


def test_decimal_difference_above_tolerance_requires_review():
    result = ReconciliationAgent().run(_context("0.30", "0.28"))
    finding = next(item for item in result.findings if item["code"] == "AMOUNT_EXCEEDS_PO")
    assert finding["message"] == "Invoice exceeds PO by 0.02 INR."
    assert result.state.value == "human_review"


def test_one_cent_difference_remains_within_existing_tolerance():
    result = ReconciliationAgent().run(_context("1.01", "1.00"))
    assert result.state.value == "complete"
    assert result.findings == []


def test_non_finite_monetary_input_fails_closed():
    result = ReconciliationAgent().run(_context("NaN", "1.00"))
    assert result.state.value == "failed"
    finding = next(item for item in result.findings if item["code"] == "RUNTIME_ERROR")
    assert "NaN" not in finding["message"]


def test_runtime_error_does_not_expose_internal_exception_details():
    class BrokenAgent(ReconciliationAgent):
        def _load(self, ctx):
            raise RuntimeError("database password=do-not-leak")

    result = BrokenAgent().run(AgentContext(invoice={}))
    assert result.state.value == "failed"
    finding = next(item for item in result.findings if item["code"] == "RUNTIME_ERROR")
    assert finding["severity"] == "high"
    assert finding["message"] == "Reconciliation could not be completed; manual review is required."
    assert "do-not-leak" not in finding["message"]


def test_database_context_preserves_decimal_values_for_reconciliation():
    invoice = SimpleNamespace(
        invoice_number="INV-DECIMAL",
        amount=Decimal("0.30"),
        currency="INR",
        supplier_id=7,
    )
    purchase_order = SimpleNamespace(
        po_number="PO-DECIMAL",
        total_amount=Decimal("0.28"),
        currency="INR",
        supplier_id=7,
    )

    context = build_reconciliation_context(invoice, purchase_order)
    assert context.invoice["amount"] is invoice.amount
    assert context.purchase_order["total_amount"] is purchase_order.total_amount

    result = ReconciliationAgent().run(context)
    assert result.state.value == "human_review"
    finding = next(item for item in result.findings if item["code"] == "AMOUNT_EXCEEDS_PO")
    assert finding["message"] == "Invoice exceeds PO by 0.02 INR."
