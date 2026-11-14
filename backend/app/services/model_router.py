"""Optional inference adapter for Ollama or OpenAI-compatible vLLM.

The model only explains deterministic findings. It never makes the reconciliation
decision, modifies records, approves invoices, or initiates payment.
"""
import os
import httpx

class ModelUnavailable(RuntimeError): pass

def explain_findings(invoice:dict,findings:list[dict],evidence:list[dict])->dict:
    prompt=(
        "You are an assistant for a finance reviewer. Explain the supplied deterministic "
        "reconciliation findings in plain language. Use only supplied evidence. Do not "
        "invent facts, make an approval decision, or authorize payment. Say when evidence "
        "is insufficient.\\n"
        f"Invoice: {invoice}\\nFindings: {findings}\\nEvidence: {evidence}"
    )
    provider=os.getenv("LLM_PROVIDER","none").lower()
    try:
        if provider=="ollama":
            base=os.getenv("OLLAMA_BASE_URL","").strip()
            model=os.getenv("OLLAMA_MODEL","qwen2.5:3b").strip()
            if not base: raise ModelUnavailable("OLLAMA_BASE_URL is not configured")
            r=httpx.post(base.rstrip("/")+"/api/generate",json={"model":model,"prompt":prompt,"stream":False},timeout=45)
            r.raise_for_status()
            return {"provider":"ollama","model":model,"explanation":r.json().get("response","").strip()}
        if provider=="vllm":
            base=os.getenv("VLLM_BASE_URL","http://localhost:8001/v1").rstrip("/")
            model=os.getenv("VLLM_MODEL","Qwen/Qwen2.5-3B-Instruct")
            key=os.getenv("VLLM_API_KEY","EMPTY")
            r=httpx.post(base+"/chat/completions",headers={"Authorization":f"Bearer {key}"},json={
                "model":model,"messages":[{"role":"user","content":prompt}],"temperature":0.1,"max_tokens":500
            },timeout=45)
            r.raise_for_status()
            return {"provider":"vllm","model":model,"explanation":r.json()["choices"][0]["message"]["content"]}
        raise ModelUnavailable("Set LLM_PROVIDER to 'ollama' or 'vllm' to enable advisory explanations")
    except ModelUnavailable:
        raise
    except Exception as exc:
        raise ModelUnavailable(f"Configured model provider failed: {type(exc).__name__}") from exc
