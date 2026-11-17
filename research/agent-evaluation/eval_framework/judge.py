"""Optional LLM-as-judge hook; never mixed into deterministic scores."""
from __future__ import annotations

from typing import Any, Callable


def run_supplementary_judge(
    cases: list[dict[str, Any]],
    judge_fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Call a caller-supplied judge for supplementary human-readable feedback.

    The caller owns provider selection, privacy review, prompt/version pinning,
    and retries. Judge scores remain a separate advisory result group.
    """
    results = []
    for case in cases:
        output = judge_fn(case)
        if not isinstance(output, dict):
            raise TypeError("Judge must return an object")
        score = output.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1:
            raise ValueError("Judge score must be numeric and between 0 and 1")
        results.append({
            "case_id": case.get("case_id"), "score": float(score),
            "rationale": str(output.get("rationale", ""))[:2000],
            "judge_model": output.get("judge_model", "unspecified"),
            "judge_prompt_version": output.get("judge_prompt_version", "unspecified"),
        })
    return {
        "method": "supplementary_llm_as_judge", "count": len(results),
        "mean_score": sum(row["score"] for row in results) / len(results) if results else None,
        "results": results,
        "warning": "Subjective and potentially biased. Do not merge with deterministic pass/fail metrics.",
    }
