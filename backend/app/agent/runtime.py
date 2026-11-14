"""Explicit deterministic state machine for financial reconciliation."""
from dataclasses import dataclass, field
from enum import Enum

class AgentState(str, Enum):
    START="start"; LOAD_RECORDS="load_records"; VALIDATE="validate"
    DECIDE="decide"; HUMAN_REVIEW="human_review"; COMPLETE="complete"; FAILED="failed"

@dataclass
class AgentContext:
    invoice: dict
    purchase_order: dict | None = None
    findings: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    state: AgentState = AgentState.START
    trace: list[str] = field(default_factory=list)

class ReconciliationAgent:
    def run(self, ctx: AgentContext) -> AgentContext:
        transitions={AgentState.START:self._load,AgentState.LOAD_RECORDS:self._validate,AgentState.VALIDATE:self._decide}
        while ctx.state in transitions:
            ctx.trace.append(ctx.state.value)
            try: transitions[ctx.state](ctx)
            except Exception as exc:
                ctx.findings.append({"code":"RUNTIME_ERROR","severity":"high","message":str(exc)})
                ctx.state=AgentState.FAILED
        ctx.trace.append(ctx.state.value)
        return ctx

    def _load(self,ctx): ctx.state=AgentState.LOAD_RECORDS

    def _validate(self,ctx):
        inv,po=ctx.invoice,ctx.purchase_order
        if not inv.get("invoice_number") or inv.get("amount") is None:
            ctx.findings.append({"code":"INCOMPLETE_INVOICE","severity":"high","message":"Invoice number or amount is missing; confirm extracted fields."})
        if not po:
            ctx.findings.append({"code":"PO_NOT_FOUND","severity":"high","message":"No linked purchase order was found."})
        else:
            if inv["currency"] != po["currency"]:
                ctx.findings.append({"code":"CURRENCY_MISMATCH","severity":"high","message":"Invoice and purchase order currencies differ."})
            if inv.get("supplier_id") is not None and po.get("supplier_id") is not None and inv["supplier_id"] != po["supplier_id"]:
                ctx.findings.append({"code":"SUPPLIER_MISMATCH","severity":"high","message":"Invoice supplier does not match purchase-order supplier."})
            if inv.get("amount") is not None:
                delta=round(float(inv["amount"])-float(po["total_amount"]),2)
                ctx.evidence.extend([
                    {"source":"purchase_order","reference":po["po_number"],"field":"total_amount","value":str(po["total_amount"])},
                    {"source":"invoice","reference":inv["invoice_number"],"field":"amount","value":str(inv["amount"])}
                ])
                if delta>0.01: ctx.findings.append({"code":"AMOUNT_EXCEEDS_PO","severity":"high","message":f"Invoice exceeds PO by {delta:.2f} {inv['currency']}."})
                elif delta < -0.01: ctx.findings.append({"code":"AMOUNT_BELOW_PO","severity":"medium","message":f"Invoice is below PO by {abs(delta):.2f} {inv['currency']}."})
        ctx.state=AgentState.VALIDATE

    def _decide(self,ctx):
        ctx.state=AgentState.HUMAN_REVIEW if ctx.findings else AgentState.COMPLETE
