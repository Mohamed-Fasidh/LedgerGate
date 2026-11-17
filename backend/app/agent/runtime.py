"""Explicit deterministic state machine for financial reconciliation."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum

CENT = Decimal("0.01")


def _money(value) -> Decimal:
    """Normalize monetary input without binary floating-point arithmetic."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid monetary amount") from None
    if not amount.is_finite():
        raise ValueError("Monetary amount must be finite")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


class AgentState(str, Enum):
    START = "start"
    LOAD_RECORDS = "load_records"
    VALIDATE = "validate"
    DECIDE = "decide"
    HUMAN_REVIEW = "human_review"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class AgentContext:
    invoice: dict
    purchase_order: dict | None = None
    findings: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    state: AgentState = AgentState.START
    trace: list[str] = field(default_factory=list)


def build_reconciliation_context(invoice, purchase_order=None) -> AgentContext:
    """Build agent input directly from ORM records without float conversion."""
    po_data = None
    if purchase_order is not None:
        po_data = {
            "po_number": purchase_order.po_number,
            "total_amount": purchase_order.total_amount,
            "currency": purchase_order.currency,
            "supplier_id": purchase_order.supplier_id,
        }
    return AgentContext(
        invoice={
            "invoice_number": invoice.invoice_number,
            "amount": invoice.amount,
            "currency": invoice.currency,
            "supplier_id": invoice.supplier_id,
        },
        purchase_order=po_data,
    )


class ReconciliationAgent:
    def run(self, ctx: AgentContext) -> AgentContext:
        transitions = {
            AgentState.START: self._load,
            AgentState.LOAD_RECORDS: self._validate,
            AgentState.VALIDATE: self._decide,
        }
        while ctx.state in transitions:
            ctx.trace.append(ctx.state.value)
            try:
                transitions[ctx.state](ctx)
            except Exception:
                # Do not return exception text to callers: it can contain SQL,
                # infrastructure details, or sensitive values from dependencies.
                ctx.findings.append({
                    "code": "RUNTIME_ERROR",
                    "severity": "high",
                    "message": "Reconciliation could not be completed; manual review is required.",
                })
                ctx.state = AgentState.FAILED
        ctx.trace.append(ctx.state.value)
        return ctx

    def _load(self, ctx):
        ctx.state = AgentState.LOAD_RECORDS

    def _validate(self, ctx):
        inv, po = ctx.invoice, ctx.purchase_order
        if not inv.get("invoice_number") or inv.get("amount") is None:
            ctx.findings.append({
                "code": "INCOMPLETE_INVOICE",
                "severity": "high",
                "message": "Invoice number or amount is missing; confirm extracted fields.",
            })
        if not po:
            ctx.findings.append({
                "code": "PO_NOT_FOUND",
                "severity": "high",
                "message": "No linked purchase order was found.",
            })
        else:
            if inv["currency"] != po["currency"]:
                ctx.findings.append({
                    "code": "CURRENCY_MISMATCH",
                    "severity": "high",
                    "message": "Invoice and purchase order currencies differ.",
                })
            if (inv.get("supplier_id") is not None
                    and po.get("supplier_id") is not None
                    and inv["supplier_id"] != po["supplier_id"]):
                ctx.findings.append({
                    "code": "SUPPLIER_MISMATCH",
                    "severity": "high",
                    "message": "Invoice supplier does not match purchase-order supplier.",
                })
            if inv.get("amount") is not None:
                delta = _money(inv["amount"]) - _money(po["total_amount"])
                ctx.evidence.extend([
                    {
                        "source": "purchase_order",
                        "reference": po["po_number"],
                        "field": "total_amount",
                        "value": str(po["total_amount"]),
                    },
                    {
                        "source": "invoice",
                        "reference": inv["invoice_number"],
                        "field": "amount",
                        "value": str(inv["amount"]),
                    },
                ])
                if delta > CENT:
                    ctx.findings.append({
                        "code": "AMOUNT_EXCEEDS_PO",
                        "severity": "high",
                        "message": f"Invoice exceeds PO by {delta:.2f} {inv['currency']}.",
                    })
                elif delta < -CENT:
                    ctx.findings.append({
                        "code": "AMOUNT_BELOW_PO",
                        "severity": "medium",
                        "message": f"Invoice is below PO by {abs(delta):.2f} {inv['currency']}.",
                    })
        ctx.state = AgentState.VALIDATE

    def _decide(self, ctx):
        ctx.state = AgentState.HUMAN_REVIEW if ctx.findings else AgentState.COMPLETE
