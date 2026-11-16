#!/usr/bin/env python3
"""Exercise PostgreSQL backup publication with a deterministic fake Docker CLI."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "scripts" / "backup_postgres.sh"

FAKE_DOCKER = r"""#!/usr/bin/env python3
import os
import sys
import time

args = sys.argv[1:]
if args[:2] == ["compose", "version"]:
    raise SystemExit(0)
if args[:3] == ["compose", "exec", "-T"] and "pg_dump" in " ".join(args):
    if os.environ.get("FAKE_PG_DUMP_FAIL") == "1":
        raise SystemExit(17)
    sys.stdout.buffer.write(b"LEDGERGATE-TEST-ARCHIVE")
    sys.stdout.flush()
    time.sleep(float(os.environ.get("FAKE_DUMP_DELAY", "0")))
    raise SystemExit(0)
if args[:3] == ["compose", "exec", "-T"] and "pg_restore" in " ".join(args):
    data = sys.stdin.buffer.read()
    if os.environ.get("FAKE_ARCHIVE_INVALID") == "1" or data != b"LEDGERGATE-TEST-ARCHIVE":
        raise SystemExit(19)
    raise SystemExit(0)
raise SystemExit("unexpected docker invocation: " + repr(args))
"""


class BackupScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        self.backups = self.root / "backups"
        self.env = os.environ.copy()
        self.env.update({
            "PATH": str(self.bin) + os.pathsep + self.env.get("PATH", ""),
            "BACKUP_DIR": str(self.backups),
            "BACKUP_RETENTION_DAYS": "30",
            "FAKE_DUMP_DELAY": "0.15",
        })
        self.env.pop("FAKE_PG_DUMP_FAIL", None)
        self.env.pop("FAKE_ARCHIVE_INVALID", None)

    def tearDown(self):
        self.temp.cleanup()

    def run_backup(self, env=None):
        return subprocess.run(
            ["bash", str(BACKUP)], cwd=ROOT,
            env=env or self.env, text=True, capture_output=True,
        )

    def test_success_publishes_valid_dump_and_checksum(self):
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        dumps = list(self.backups.glob("ledgergate-*.dump"))
        self.assertEqual(len(dumps), 1)
        dump = dumps[0]
        self.assertEqual(dump.read_bytes(), b"LEDGERGATE-TEST-ARCHIVE")
        sidecar = Path(str(dump) + ".sha256")
        self.assertTrue(sidecar.is_file())
        expected = hashlib.sha256(dump.read_bytes()).hexdigest()
        self.assertIn(expected, sidecar.read_text(encoding="utf-8"))
        self.assertFalse(list(self.backups.glob("*.partial")))
        self.assertEqual(dump.stat().st_mode & 0o777, 0o600)

    def test_overlapping_backups_publish_distinct_files(self):
        processes = [
            subprocess.Popen(
                ["bash", str(BACKUP)], cwd=ROOT, env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(2)
        ]
        results = [process.communicate(timeout=30) + (process.returncode,) for process in processes]
        self.assertTrue(all(code == 0 for _, _, code in results), results)
        dumps = list(self.backups.glob("ledgergate-*.dump"))
        self.assertEqual(len(dumps), 2)
        self.assertEqual(len({path.name for path in dumps}), 2)
        self.assertTrue(all(Path(str(path) + ".sha256").is_file() for path in dumps))

    def test_failed_dump_does_not_publish_partial_backup(self):
        env = dict(self.env, FAKE_PG_DUMP_FAIL="1")
        result = self.run_backup(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.backups.glob("ledgergate-*.dump")), [])
        self.assertEqual(list(self.backups.glob("*.partial")), [])

    def test_invalid_archive_does_not_publish_backup(self):
        env = dict(self.env, FAKE_ARCHIVE_INVALID="1")
        result = self.run_backup(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.backups.glob("ledgergate-*.dump")), [])
        self.assertEqual(list(self.backups.glob("*.partial")), [])

    def test_invalid_retention_is_rejected_before_backup(self):
        env = dict(self.env, BACKUP_RETENTION_DAYS="0")
        result = self.run_backup(env)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.backups.glob("ledgergate-*.dump")), [])


if __name__ == "__main__":
    unittest.main()
