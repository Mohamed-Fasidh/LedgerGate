#!/usr/bin/env python3
"""Fail when a GitHub Actions workflow references a mutable action tag."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
USES_LINE = re.compile(r"^\s*uses:\s*([^\s#]+)")


def main() -> int:
    failures: list[str] = []
    for workflow in sorted((*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml"))):
        for line_number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = USES_LINE.match(line)
            if not match:
                continue
            action = match.group(1)
            if action.startswith("./"):
                continue
            if "@" not in action:
                failures.append(f"{workflow.relative_to(ROOT)}:{line_number}: action reference has no ref: {action}")
                continue
            ref = action.rsplit("@", 1)[1]
            if not re.fullmatch(r"[0-9a-fA-F]{40}", ref):
                failures.append(
                    f"{workflow.relative_to(ROOT)}:{line_number}: action ref must be a full 40-character commit SHA: {action}"
                )

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("All third-party GitHub Actions are pinned to full commit SHAs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
