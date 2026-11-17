from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


class ExperimentTracker:
    """Append-only local tracker that writes JSON and CSV without SaaS services."""

    def __init__(self, directory: str | Path, name: str, config: dict[str, Any]):
        self.directory = Path(directory) / name
        self.directory.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.config = config
        self.rows: list[dict[str, Any]] = []
        self.metadata = {"experiment": name, "config": config}

    def log(self, **metrics: Any) -> None:
        row = {
            key: (value.item() if hasattr(value, "item") else value)
            for key, value in metrics.items()
        }
        self.rows.append(row)

    def close(self, summary: dict[str, Any] | None = None) -> None:
        summary = summary or {}
        self.metadata["summary"] = summary
        self.metadata["history"] = self.rows
        (self.directory / "metrics.json").write_text(
            json.dumps(self.metadata, indent=2, allow_nan=False), encoding="utf-8"
        )
        columns: list[str] = []
        for row in self.rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        with (self.directory / "metrics.csv").open(
            "w", newline="", encoding="utf-8"
        ) as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            if columns:
                writer.writeheader()
                writer.writerows(self.rows)
        (self.directory / "summary.json").write_text(
            json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8"
        )
