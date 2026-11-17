from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

VALID_DECISIONS = {"accepted", "rejected", "escalated", "needs_more_evidence"}


def record_human_review(queue_path: Path, case_id: str, reviewer_id: str,
                        decision: str, rationale: str) -> Path:
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(sorted(VALID_DECISIONS))}")
    if not reviewer_id.strip() or len(reviewer_id) > 120:
        raise ValueError("A reviewer ID of 1-120 characters is required")
    if len(rationale) > 2000:
        raise ValueError("Rationale must not exceed 2000 characters")
    candidates = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if case_id not in {item["case_id"] for item in candidates}:
        raise KeyError(f"Case {case_id} is not in this review queue")
    record = {"case_id": case_id, "reviewer_id": reviewer_id, "decision": decision,
              "rationale": rationale, "reviewed_at": datetime.now(timezone.utc).isoformat()}
    path = queue_path.with_name("human_reviews.jsonl")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
