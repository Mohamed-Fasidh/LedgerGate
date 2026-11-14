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
