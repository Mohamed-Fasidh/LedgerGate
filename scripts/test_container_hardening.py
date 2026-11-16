from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ContainerHardeningTests(unittest.TestCase):
    def test_api_image_uses_dedicated_non_root_uid(self):
        dockerfile = (ROOT / "backend" / "Dockerfile").read_text()
        self.assertIn("groupadd --system --gid 10001 ledgergate", dockerfile)
        self.assertIn("useradd --system --uid 10001", dockerfile)
        self.assertIn("chown 10001:10001 /app/uploads", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertNotIn("alembic upgrade", dockerfile)
        self.assertNotIn("bootstrap_runtime_role.sh &&", dockerfile)

    def test_frontend_image_runs_as_node_user(self):
        dockerfile = (ROOT / "frontend" / "Dockerfile").read_text()
        self.assertIn("USER node", dockerfile)

    def test_compose_healthchecks_gate_frontend_on_api(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("http://127.0.0.1:8000/health", compose)
        self.assertIn("http://127.0.0.1:3000", compose)
        self.assertIn("condition: service_healthy", compose)


if __name__ == "__main__":
    unittest.main()
