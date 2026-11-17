from __future__ import annotations

import re
from collections import Counter
from math import sqrt


def tokens(text: str) -> set[str]:
    return {item for item in re.findall(r"[a-z0-9]{2,}", text.lower())}


def char_ngrams(text: str, n: int = 3) -> Counter:
    clean = " ".join(sorted(tokens(text)))
    return Counter(clean[index:index+n] for index in range(max(0, len(clean)-n+1)))


def cosine(left: Counter, right: Counter) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0) for key, value in left.items())
    norm_left = sqrt(sum(value * value for value in left.values()))
    norm_right = sqrt(sum(value * value for value in right.values()))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def hybrid_score(query: str, document: str) -> float:
    query_tokens, doc_tokens = tokens(query), tokens(document)
    lexical = len(query_tokens & doc_tokens) / max(1, len(query_tokens))
    fuzzy = cosine(char_ngrams(query), char_ngrams(document))
    return round(0.7 * lexical + 0.3 * fuzzy, 4)


def rank_documents(query: str, documents: list[dict]) -> list[dict]:
    ranked = []
    for document in documents:
        text = f"{document.get('title', '')} {document.get('body', '')}"
        ranked.append({"id": document["id"], "score": hybrid_score(query, text)})
    return sorted(ranked, key=lambda item: (-item["score"], item["id"]))
