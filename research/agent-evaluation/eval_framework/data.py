from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SUITES = {
    "agent": "agent_traces.jsonl",
    "tools": "tool_traces.jsonl",
    "retrieval": "retrieval_cases.jsonl",
    "claims": "claims.jsonl",
    "recovery": "failure_recovery.jsonl",
    "multistep": "multistep.jsonl",
    "financial": "financial.jsonl",
    "security": "security.jsonl",
    "live_reconciliation": "live_reconciliation.jsonl",
}


def read_jsonl(path: Path, id_field: str = "case_id") -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc.msg}") from None
        if not isinstance(value, dict) or not isinstance(value.get(id_field), str):
            raise ValueError(f"Each JSONL row must be an object with string {id_field}: {path}:{line_number}")
        rows.append(value)
    return rows


def load_dataset(dataset_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    manifest_path = dataset_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_version") != "1.0.0":
        raise ValueError("Unsupported dataset version")
    datasets = {}
    expected_files = manifest.get("files", {})
    for suite, filename in SUITES.items():
        path = dataset_dir / filename
        if not path.exists():
            continue
        content = path.read_bytes()
        declared = expected_files.get(filename)
        if declared and hashlib.sha256(content).hexdigest() != declared:
            raise ValueError(f"Dataset hash mismatch: {filename}")
        datasets[suite] = read_jsonl(path)
    return manifest, datasets


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
