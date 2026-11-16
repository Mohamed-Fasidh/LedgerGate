#!/usr/bin/env python3
"""Guard the production API image against per-replica privileged migrations."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProductionContainerStartupTests(unittest.TestCase):
    def test_api_image_starts_only_the_application_server(self):
        dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
        cmd = next((line for line in dockerfile.splitlines() if line.startswith("CMD ")), "")
        self.assertIn('"uvicorn"', cmd)
        self.assertNotIn("alembic upgrade", cmd)
        self.assertNotIn("bootstrap_runtime_role", cmd)

    def test_local_compose_keeps_developer_bootstrap_override(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        api = compose.split("\n  clamav:", 1)[0]
        self.assertIn("alembic upgrade head", api)
        self.assertIn("bootstrap_runtime_role.sh", api)

    def test_deployment_guide_requires_one_shot_release_job(self):
        guide = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
        self.assertIn("one-shot release/bootstrap task", guide)
        self.assertIn("does not run migrations or create database roles on startup", guide)


if __name__ == "__main__":
    unittest.main()
