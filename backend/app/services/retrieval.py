"""Tenant-scoped hybrid lexical retrieval baseline.

Combines token overlap with character n-gram similarity. This is a lightweight,
deterministic hybrid baseline, not a neural embedding/vector-search claim.
"""
import re
from collections import Counter
from math import sqrt

from sqlalchemy.orm import Session

from app.models import PolicyDocument, PurchaseOrder, Supplier


MAX_RETRIEVAL_CANDIDATES = 1000
MAX_RETRIEVAL_RESULTS = 50
MAX_QUERY_LENGTH = 500


def tokens(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9]{2,}", text.lower())}


def char_ngrams(text: str, n: int = 3) -> Counter:
    # Sorting avoids process-dependent scores caused by set iteration order.
    clean = " ".join(sorted(tokens(text)))
    return Counter(clean[i : i + n] for i in range(max(0, len(clean) - n + 1)))


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = sqrt(sum(v * v for v in a.values()))
    nb = sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def hybrid_score(query: str, document: str) -> float:
    q = tokens(query)
    d = tokens(document)
    lexical = len(q & d) / max(1, len(q))
    fuzzy = cosine(char_ngrams(query), char_ngrams(document))
    return round(0.7 * lexical + 0.3 * fuzzy, 4)


def _bounded_limit(limit: int, default: int) -> int:
    """Keep caller-controlled result sizes within a small, predictable bound."""
    try:
        requested = int(limit)
    except (TypeError, ValueError, OverflowError):
        requested = default
    return max(1, min(requested, MAX_RETRIEVAL_RESULTS))


def _normalise_query(query: str) -> str:
    if not isinstance(query, str):
        return ""
    return query.strip()[:MAX_QUERY_LENGTH]


def retrieve_policy(db: Session, tenant_id: str, query: str, limit: int = 5):
    query = _normalise_query(query)
    if not query or not tenant_id:
        return []

    result_limit = _bounded_limit(limit, 5)
    # Bound database work even when a tenant has accumulated many policy docs.
    docs = (
        db.query(PolicyDocument)
        .filter(PolicyDocument.tenant_id == tenant_id)
        .order_by(PolicyDocument.id.desc())
        .limit(MAX_RETRIEVAL_CANDIDATES)
        .all()
    )
    ranked = []
    for doc in docs:
        score = hybrid_score(query, f"{doc.title} {doc.body}")
        if score > 0:
            ranked.append(
                {
                    "id": doc.id,
                    "title": doc.title,
                    "snippet": doc.body[:500],
                    "score": score,
                    "retrieval": "hybrid_lexical_fuzzy",
                }
            )
    return sorted(ranked, key=lambda x: (-x["score"], x["id"]))[:result_limit]


def retrieve_business_records(db: Session, tenant_id: str, query: str, limit: int = 10):
    query = _normalise_query(query)
    if not query or not tenant_id:
        return []

    result_limit = _bounded_limit(limit, 10)
    # Fetch a bounded candidate window, then load all matching suppliers in one
    # tenant-scoped query instead of issuing one query per purchase order.
    pos = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.tenant_id == tenant_id)
        .order_by(PurchaseOrder.id.desc())
        .limit(MAX_RETRIEVAL_CANDIDATES)
        .all()
    )
    supplier_ids = {po.supplier_id for po in pos}
    suppliers = {}
    if supplier_ids:
        suppliers = {
            supplier.id: supplier
            for supplier in (
                db.query(Supplier)
                .filter(
                    Supplier.tenant_id == tenant_id,
                    Supplier.id.in_(supplier_ids),
                )
                .all()
            )
        }

    rows = []
    for po in pos:
        supplier = suppliers.get(po.supplier_id)
        text = f"{po.po_number} {po.description or ''} {supplier.name if supplier else ''}"
        score = hybrid_score(query, text)
        if score > 0:
            rows.append(
                {
                    "type": "purchase_order",
                    "id": po.id,
                    "reference": po.po_number,
                    "supplier": supplier.name if supplier else None,
                    # Keep exact currency precision for agent/tool consumers.
                    "amount": str(po.total_amount) if po.total_amount is not None else None,
                    "currency": po.currency,
                    "score": score,
                }
            )
    return sorted(rows, key=lambda x: (-x["score"], x["id"]))[:result_limit]
