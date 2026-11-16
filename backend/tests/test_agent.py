from app.agent.runtime import AgentContext, ReconciliationAgent

def test_amount_discrepancy_requires_human_review():
    ctx=AgentContext(invoice={"invoice_number":"INV-1","amount":65000,"currency":"INR"},
                     purchase_order={"po_number":"PO-1","total_amount":50000,"currency":"INR"})
    result=ReconciliationAgent().run(ctx)
    assert result.state.value=="human_review"
    assert any(x["code"]=="AMOUNT_EXCEEDS_PO" for x in result.findings)

def test_exact_match_completes():
    ctx=AgentContext(invoice={"invoice_number":"INV-2","amount":50000,"currency":"INR"},
                     purchase_order={"po_number":"PO-2","total_amount":50000,"currency":"INR"})
    result=ReconciliationAgent().run(ctx)
    assert result.state.value=="complete"
    assert result.findings==[]

def test_runtime_error_does_not_expose_internal_exception_details():
    class BrokenAgent(ReconciliationAgent):
        def _load(self, ctx):
            raise RuntimeError("database password=do-not-leak")

    result=BrokenAgent().run(AgentContext(invoice={}))
    assert result.state.value == "failed"
    finding = next(item for item in result.findings if item["code"] == "RUNTIME_ERROR")
    assert finding["severity"] == "high"
    assert finding["message"] == "Reconciliation could not be completed; manual review is required."
    assert "do-not-leak" not in finding["message"]
