from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid monetary value") from None
    if not amount.is_finite():
        raise ValueError("Monetary value must be finite")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_invoice_total(case: dict[str, Any]) -> dict[str, str]:
    subtotal = ZERO
    for line in case["lines"]:
        quantity = Decimal(str(line["quantity"]))
        unit_price = Decimal(str(line["unit_price"]))
        if not quantity.is_finite() or not unit_price.is_finite() or quantity < 0 or unit_price < 0:
            raise ValueError("Quantity and unit price must be finite and non-negative")
        subtotal += (quantity * unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
    subtotal = subtotal.quantize(CENT, rounding=ROUND_HALF_UP)
    tax_rate = Decimal(str(case["tax_rate"]))
    discount = money(case.get("discount", "0.00"))
    if not tax_rate.is_finite() or tax_rate < 0 or tax_rate > 1:
        raise ValueError("Tax rate must be between zero and one")
    if discount < 0 or discount > subtotal:
        raise ValueError("Discount must be between zero and subtotal")
    tax = (subtotal * tax_rate).quantize(CENT, rounding=ROUND_HALF_UP)
    total = subtotal + tax - discount
    return {"subtotal": str(subtotal), "tax": str(tax),
            "total": str(total.quantize(CENT, rounding=ROUND_HALF_UP))}


def amount_reconciliation_state(invoice_amount: Any, po_amount: Any, tolerance: str = "0.01") -> str:
    delta = money(invoice_amount) - money(po_amount)
    return "complete" if abs(delta) <= Decimal(tolerance) else "human_review"
