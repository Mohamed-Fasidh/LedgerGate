"""Reproducible deterministic evaluation suite for the reconciliation state machine."""
from app.agent.runtime import AgentContext, ReconciliationAgent

CASES = [
    {"name":"exact_match","invoice":50000,"po":50000,"currency":"INR","expected":"complete"},
    {"name":"over_billed","invoice":65000,"po":50000,"currency":"INR","expected":"human_review"},
    {"name":"under_billed","invoice":45000,"po":50000,"currency":"INR","expected":"human_review"},
    {"name":"currency_mismatch","invoice":50000,"po":50000,"currency":"USD","po_currency":"INR","expected":"human_review"},
    {"name":"missing_po","invoice":50000,"po":None,"currency":"INR","expected":"human_review"},
]
def run_evaluation():
    results=[]
    for case in CASES:
        po = None if case["po"] is None else {"po_number":"PO-EVAL","total_amount":case["po"],"currency":case.get("po_currency",case["currency"])}
        ctx=AgentContext(invoice={"invoice_number":case["name"],"amount":case["invoice"],"currency":case["currency"]},purchase_order=po)
        out=ReconciliationAgent().run(ctx)
        passed=out.state.value==case["expected"]
        results.append({"case":case["name"],"expected":case["expected"],"actual":out.state.value,"passed":passed,"findings":out.findings})
    return {"total":len(results),"passed":sum(x["passed"] for x in results),"accuracy":sum(x["passed"] for x in results)/len(results),"results":results}
