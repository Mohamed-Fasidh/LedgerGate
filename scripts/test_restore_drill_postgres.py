#!/usr/bin/env python3
"""Exercise disposable PostgreSQL restore drills with a fake Docker CLI."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RESTORE = ROOT / "scripts" / "restore_drill_postgres.sh"

FAKE_DOCKER = r"""#!/usr/bin/env python3
import os
from pathlib import Path
import sys

args = sys.argv[1:]
state = Path(os.environ["FAKE_DB_STATE"])
state.mkdir(parents=True, exist_ok=True)

if args[:2] == ["compose", "version"]:
    raise SystemExit(0)
if args[:3] == ["compose", "exec", "-T"] and "pg_restore" in " ".join(args):
    data = sys.stdin.buffer.read()
    if data != b"LEDGERGATE-TEST-ARCHIVE":
        raise SystemExit(21)
    if "--list" in args:
        raise SystemExit(0)
    db_name = args[-1]
    if not (state / (db_name + ".created")).exists():
        raise SystemExit("restore target was not created")
    if os.environ.get("FAKE_RESTORE_FAIL") == "1":
        raise SystemExit(22)
    (state / (db_name + ".restored")).write_text("yes", encoding="utf-8")
    raise SystemExit(0)
if args[:3] == ["compose", "exec", "-T"] and "sh" in args and "-c" in args:
    command_index = args.index("-c")
    command = args[command_index + 1]
    db_name = args[-1]
    if "createdb" in command:
        (state / (db_name + ".created")).write_text("yes", encoding="utf-8")
        raise SystemExit(0)
    if "dropdb" in command:
        for suffix in (".created", ".restored"):
            (state / (db_name + suffix)).unlink(missing_ok=True)
        raise SystemExit(0)
    if "psql" in command:
        sql = sys.stdin.read()
        if not (state / (db_name + ".restored")).exists():
            raise SystemExit("database was not restored before verification")
        valid = os.environ.get("FAKE_SCHEMA_VALID", "1") == "1"
        if "alembic_version" not in sql or "public.users" not in sql or "public.invoices" not in sql:
            raise SystemExit("restore verification omitted a required object")
        print("true" if valid else "false")
        raise SystemExit(0)
raise SystemExit("unexpected docker invocation: " + repr(args))
"""


class RestoreDrillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        self.state = self.root / "db-state"
        self.backup = self.root / "sample.dump"
        self.backup.write_bytes(b"LEDGERGATE-TEST-ARCHIVE")
        digest = hashlib.sha256(self.backup.read_bytes()).hexdigest()
        Path(str(self.backup) + ".sha256").write_text(
            f"{digest}  {self.backup.name}\n", encoding="utf-8"
        )
        self.env = os.environ.copy()
        self.env.update({
            "PATH": str(self.bin) + os.pathsep + self.env.get("PATH", ""),
            "FAKE_DB_STATE": str(self.state),
            "FAKE_SCHEMA_VALID": "1",
        })
        self.env.pop("FAKE_RESTORE_FAIL", None)

    def tearDown(self):
        self.temp.cleanup()

    def run_restore(self, env=None):
        return subprocess.run(
            ["bash", str(RESTORE), str(self.backup)], cwd=ROOT,
            env=env or self.env, text=True, capture_output=True,
        )

    def database_files(self):
        return list(self.state.glob("ledgergate_restore_drill_*.created"))

    def test_success_restores_verifies_and_drops_disposable_database(self):
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Restore drill passed", result.stdout)
        self.assertEqual(self.database_files(), [])
        self.assertEqual(list(self.state.glob("*.restored")), [])

    def test_failed_restore_cleans_disposable_database(self):
        env = dict(self.env, FAKE_RESTORE_FAIL="1")
        result = self.run_restore(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.database_files(), [])
        self.assertEqual(list(self.state.glob("*.restored")), [])

    def test_failed_schema_verification_cleans_disposable_database(self):
        env = dict(self.env, FAKE_SCHEMA_VALID="0")
        result = self.run_restore(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("core users/invoices tables", result.stderr)
        self.assertEqual(self.database_files(), [])

    def test_keep_database_flag_preserves_successful_restore(self):
        env = dict(self.env, RESTORE_DRILL_KEEP_DB="1")
        result = self.run_restore(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.database_files()), 1)
        self.assertEqual(len(list(self.state.glob("*.restored"))), 1)

    def test_checksum_mismatch_fails_before_database_creation(self):
        self.backup.write_bytes(b"tampered archive")
        result = self.run_restore()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.database_files(), [])

    def test_missing_checksum_fails_before_database_creation(self):
        Path(str(self.backup) + ".sha256").unlink()
        result = self.run_restore()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.database_files(), [])


if __name__ == "__main__":
    unittest.main()
