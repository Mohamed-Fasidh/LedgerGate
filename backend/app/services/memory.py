"""Tenant-isolated lightweight episodic memory with deterministic relevance ranking."""
import re
from app.models import AgentMemory

def _tokens(s):
    return {t for t in re.findall(r"[a-z0-9]{2,}",s.lower())}

def remember(db,tenant_id,content,memory_type="episodic",metadata=None):
    if memory_type not in {"episodic","semantic","procedural"}:
        raise ValueError("Unsupported memory type")
    obj=AgentMemory(tenant_id=tenant_id,memory_type=memory_type,content=content,metadata_json=metadata or {})
    db.add(obj); db.flush()
    return obj

def recall(db,tenant_id,query,limit=8):
    q=_tokens(query)
    candidates=db.query(AgentMemory).filter(AgentMemory.tenant_id==tenant_id).order_by(AgentMemory.created_at.desc()).limit(500).all()
    ranked=[]
    for m in candidates:
        overlap=len(q&_tokens(m.content))
        if overlap:
            ranked.append({"id":m.id,"type":m.memory_type,"content":m.content,"metadata":m.metadata_json,"score":round(overlap/max(1,len(q)),4)})
    return sorted(ranked,key=lambda x:(x["score"],x["id"]),reverse=True)[:limit]
