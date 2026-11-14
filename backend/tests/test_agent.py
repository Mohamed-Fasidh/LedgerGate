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
