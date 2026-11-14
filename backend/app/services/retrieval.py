"""Tenant-scoped hybrid lexical retrieval baseline.

Combines token overlap with character n-gram similarity. This is a lightweight,
deterministic hybrid baseline, not a neural embedding/vector-search claim.
"""
import re
from collections import Counter
from math import sqrt
from sqlalchemy.orm import Session
from app.models import PolicyDocument, PurchaseOrder, Supplier

def tokens(text:str)->set[str]:
    return {x for x in re.findall(r"[a-z0-9]{2,}",text.lower())}

def char_ngrams(text:str,n=3)->Counter:
    clean=" ".join(tokens(text))
    return Counter(clean[i:i+n] for i in range(max(0,len(clean)-n+1)))

def cosine(a:Counter,b:Counter)->float:
    if not a or not b: return 0.0
    dot=sum(v*b.get(k,0) for k,v in a.items())
    na=sqrt(sum(v*v for v in a.values())); nb=sqrt(sum(v*v for v in b.values()))
    return dot/(na*nb) if na and nb else 0.0

def hybrid_score(query:str,document:str)->float:
    q=tokens(query); d=tokens(document)
    lexical=len(q&d)/max(1,len(q))
    fuzzy=cosine(char_ngrams(query),char_ngrams(document))
    return round(0.7*lexical+0.3*fuzzy,4)

def retrieve_policy(db:Session,tenant_id:str,query:str,limit:int=5):
    docs=db.query(PolicyDocument).filter(PolicyDocument.tenant_id==tenant_id).all()
    ranked=[]
    for doc in docs:
        score=hybrid_score(query,f"{doc.title} {doc.body}")
        if score>0:
            ranked.append({"id":doc.id,"title":doc.title,"snippet":doc.body[:500],"score":score,"retrieval":"hybrid_lexical_fuzzy"})
    return sorted(ranked,key=lambda x:x["score"],reverse=True)[:limit]

def retrieve_business_records(db:Session,tenant_id:str,query:str,limit:int=10):
    rows=[]
    pos=db.query(PurchaseOrder).filter(PurchaseOrder.tenant_id==tenant_id).all()
    for po in pos:
        supplier=db.query(Supplier).filter(Supplier.id==po.supplier_id,Supplier.tenant_id==tenant_id).first()
        text=f"{po.po_number} {po.description or ''} {supplier.name if supplier else ''}"
        score=hybrid_score(query,text)
        if score>0:
            rows.append({"type":"purchase_order","id":po.id,"reference":po.po_number,"supplier":supplier.name if supplier else None,"amount":float(po.total_amount),"currency":po.currency,"score":score})
    return sorted(rows,key=lambda x:x["score"],reverse=True)[:limit]
