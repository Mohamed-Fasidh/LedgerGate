from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.agent.tool_executor import ToolError, ToolExecutor
from app.agent.tools import get_invoice, get_purchase_order


def test_unknown_tool_rejected():
    class Dummy:
        pass

    executor = ToolExecutor(Dummy(), "tenant-a")
    with pytest.raises(ToolError):
        executor.execute("shell", {})


def test_invalid_payload_rejected():
    class Dummy:
        pass

    executor = ToolExecutor(Dummy(), "tenant-a")
    with pytest.raises(ToolError):
        executor.execute("invoice_lookup", {"invoice_id": -1})


def test_validation_error_does_not_echo_sensitive_input():
    class Dummy:
        pass

    executor = ToolExecutor(Dummy(), "tenant-a")
    secret = "CONFIDENTIAL-INVOICE-REFERENCE-" * 20
    with pytest.raises(ToolError) as exc:
        executor.execute("po_search", {"query": secret})
    assert str(exc.value) == "Invalid tool arguments"
    assert secret not in str(exc.value)
    assert secret not in repr(exc.value.__cause__)


class FakeQuery:
    def __init__(self, row):
        self.row = row

    def filter(self, *conditions):
        return self

    def first(self):
        return self.row


class FakeDB:
    def __init__(self, row):
        self.row = row

    def query(self, model):
        return FakeQuery(self.row)


def test_tool_executor_preserves_invoice_decimal_as_string():
    row = SimpleNamespace(
        id=1,
        invoice_number="INV-1",
        amount=Decimal("0.10"),
        status=SimpleNamespace(value="received"),
    )
    result = ToolExecutor(FakeDB(row), "tenant-a").execute(
        "invoice_lookup", {"invoice_id": 1}
    )
    assert result["amount"] == "0.10"
    assert isinstance(result["amount"], str)


def test_mcp_invoice_tool_preserves_invoice_decimal_as_string():
    row = SimpleNamespace(
        id=1, invoice_number="INV-1", amount=Decimal("0.10"), currency="INR"
    )
    result = get_invoice(FakeDB(row), "tenant-a", 1)
    assert result["amount"] == "0.10"
    assert isinstance(result["amount"], str)


def test_mcp_purchase_order_tool_preserves_decimal_as_string():
    row = SimpleNamespace(
        id=1, po_number="PO-1", total_amount=Decimal("0.20"), currency="INR"
    )
    result = get_purchase_order(FakeDB(row), "tenant-a", 1)
    assert result["total_amount"] == "0.20"
    assert isinstance(result["total_amount"], str)
