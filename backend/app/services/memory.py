"""Tenant-isolated lightweight episodic memory with deterministic relevance ranking."""
import re
from app.models import AgentMemory

MAX_MEMORY_QUERY_LENGTH = 500
MAX_MEMORY_RESULTS = 20
MAX_MEMORY_CANDIDATES = 500

def _tokens(s):
    return {t for t in re.findall(r"[a-z0-9]{2,}",s.lower())}

def remember(db,tenant_id,content,memory_type="episodic",metadata=None):
    if memory_type not in {"episodic","semantic","procedural"}:
        raise ValueError("Unsupported memory type")
    obj=AgentMemory(tenant_id=tenant_id,memory_type=memory_type,content=content,metadata_json=metadata or {})
    db.add(obj); db.flush()
    return obj

def recall(db,tenant_id,query,limit=8):
    if not isinstance(query, str) or len(query.strip()) < 2:
        raise ValueError("Memory search query must contain at least two characters")
    if len(query) > MAX_MEMORY_QUERY_LENGTH:
        raise ValueError("Memory search query exceeds the 500-character limit")
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("Memory search result limit must be an integer")
    limit = max(1, min(limit, MAX_MEMORY_RESULTS))
    q=_tokens(query)
    candidates=db.query(AgentMemory).filter(AgentMemory.tenant_id==tenant_id).order_by(AgentMemory.created_at.desc()).limit(MAX_MEMORY_CANDIDATES).all()
    ranked=[]
    for m in candidates:
        overlap=len(q&_tokens(m.content))
        if overlap:
            ranked.append({"id":m.id,"type":m.memory_type,"content":m.content,"metadata":m.metadata_json,"score":round(overlap/max(1,len(q)),4)})
    return sorted(ranked,key=lambda x:(x["score"],x["id"]),reverse=True)[:limit]
