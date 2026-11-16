#!/usr/bin/env python3
"""Exercise workflow-run cleanup with a deterministic fake GitHub CLI."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cleanup_workflow_runs.sh"

FAKE_GH = r"""#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")

if "--paginate" in args:
    # Simulate the jq selection applied by the real gh CLI.
    query = args[args.index("--jq") + 1]
    assert 'select(.conclusion == "failure" or .conclusion == "cancelled")' in query
    runs = [
        {"id": 101, "conclusion": "failure"},
        {"id": 102, "conclusion": "cancelled"},
        {"id": 103, "conclusion": "success"},
        {"id": 104, "conclusion": "timed_out"},
    ]
    for run in runs:
        if run["conclusion"] in ("failure", "cancelled"):
            print(run["id"])
    raise SystemExit(0)

if "--method" in args and "DELETE" in args:
    raise SystemExit(0)

raise SystemExit("unexpected gh invocation: " + repr(args))
"""


class WorkflowCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "gh"
        fake.write_text(FAKE_GH, encoding="utf-8")
        fake.chmod(0o755)
        self.log = self.root / "gh.log"
        self.env = os.environ.copy()
        self.env.update({
            "PATH": str(self.bin) + os.pathsep + self.env.get("PATH", ""),
            "GITHUB_REPOSITORY": "example/ledgergate",
            "GH_TOKEN": "test-token",
            "FAKE_GH_LOG": str(self.log),
        })

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, env=None):
        return subprocess.run(
            ["bash", str(SCRIPT)], cwd=ROOT,
            env=env or self.env, text=True, capture_output=True,
        )

    def test_deletes_only_failed_and_cancelled_completed_runs(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertIn("--paginate", calls[0])
        self.assertEqual(
            [call[-1] for call in calls[1:]],
            ["/repos/example/ledgergate/actions/runs/101",
             "/repos/example/ledgergate/actions/runs/102"],
        )
        self.assertIn("deleted 2", result.stdout.lower())

    def test_requires_repository_and_token(self):
        env = self.env.copy()
        env.pop("GH_TOKEN")
        result = self.run_script(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GH_TOKEN is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
