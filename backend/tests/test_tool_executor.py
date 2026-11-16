import pytest
from app.agent.tool_executor import ToolExecutor, ToolError

def test_unknown_tool_rejected():
    class Dummy: pass
    executor=ToolExecutor(Dummy(), "tenant-a")
    with pytest.raises(ToolError):
        executor.execute("shell",{})

def test_invalid_payload_rejected():
    class Dummy: pass
    executor=ToolExecutor(Dummy(), "tenant-a")
    with pytest.raises(ToolError):
        executor.execute("invoice_lookup",{"invoice_id":-1})

def test_validation_error_does_not_echo_sensitive_input():
    class Dummy: pass
    executor=ToolExecutor(Dummy(), "tenant-a")
    secret = "CONFIDENTIAL-INVOICE-REFERENCE-" * 20
    with pytest.raises(ToolError) as exc:
        executor.execute("po_search", {"query": secret})
    assert str(exc.value) == "Invalid tool arguments"
    assert secret not in str(exc.value)
    assert secret not in repr(exc.value.__cause__)
