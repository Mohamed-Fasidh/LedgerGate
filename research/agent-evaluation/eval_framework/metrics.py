from __future__ import annotations

from math import log2
from statistics import mean
from typing import Any, Iterable


def safe_rate(numerator: int | float, denominator: int | float) -> float | None:
    return float(numerator / denominator) if denominator else None


def classification_metrics(rows: Iterable[dict[str, Any]], expected_key: str,
                           actual_key: str, positive: Any = True) -> dict[str, Any]:
    rows = list(rows)
    tp = sum(r.get(expected_key) == positive and r.get(actual_key) == positive for r in rows)
    fp = sum(r.get(expected_key) != positive and r.get(actual_key) == positive for r in rows)
    fn = sum(r.get(expected_key) == positive and r.get(actual_key) != positive for r in rows)
    tn = sum(r.get(expected_key) != positive and r.get(actual_key) != positive for r in rows)
    precision = safe_rate(tp, tp + fp)
    recall = safe_rate(tp, tp + fn)
    f1 = safe_rate(2 * precision * recall, precision + recall) if precision is not None and recall is not None and precision + recall else 0.0
    return {"count": len(rows), "correct": tp + tn, "accuracy": safe_rate(tp + tn, len(rows)),
            "precision": precision, "recall": recall, "f1": f1,
            "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn}}


def retrieval_metrics(ranked_ids: list[str], relevance: dict[str, int], k: int) -> dict[str, float | int | None]:
    k = max(1, int(k))
    ranked = ranked_ids[:k]
    relevant = {key for key, grade in relevance.items() if grade > 0}
    hits = sum(item in relevant for item in ranked)
    precision = safe_rate(hits, k)
    recall = safe_rate(hits, len(relevant))
    rr = next((1.0 / rank for rank, item in enumerate(ranked, 1) if item in relevant), 0.0)
    gains = [((2 ** relevance.get(item, 0)) - 1) / log2(rank + 1) for rank, item in enumerate(ranked, 1)]
    ideal_grades = sorted(relevance.values(), reverse=True)[:k]
    ideal = sum(((2 ** grade) - 1) / log2(rank + 1) for rank, grade in enumerate(ideal_grades, 1))
    return {"k": k, "retrieved_relevant": hits, "relevant_total": len(relevant),
            "precision_at_k": precision, "recall_at_k": recall,
            "reciprocal_rank": rr, "ndcg_at_k": sum(gains) / ideal if ideal else 0.0}


def percentile(values: Iterable[float], p: float) -> float | None:
    items = sorted(float(value) for value in values)
    if not 0 <= p <= 100:
        raise ValueError("percentile must be in [0, 100]")
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    position = (len(items) - 1) * p / 100
    low = int(position)
    high = min(low + 1, len(items) - 1)
    fraction = position - low
    return items[low] * (1 - fraction) + items[high] * fraction


def summarize_durations_ms(values: Iterable[float]) -> dict[str, float | int | None]:
    items = [float(value) for value in values]
    if not items:
        return {"count": 0, "mean_ms": None, "p50_ms": None, "p95_ms": None,
                "p99_ms": None, "min_ms": None, "max_ms": None}
    return {"count": len(items), "mean_ms": mean(items), "p50_ms": percentile(items, 50),
            "p95_ms": percentile(items, 95), "p99_ms": percentile(items, 99),
            "min_ms": min(items), "max_ms": max(items)}
