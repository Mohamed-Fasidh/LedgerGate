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
        self.assertIn("chown -R node:node /app", dockerfile)
        self.assertIn("USER node", dockerfile)

    def test_compose_healthchecks_gate_frontend_on_api(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("http://127.0.0.1:8000/ready", compose)
        self.assertIn("http://127.0.0.1:3000", compose)
        self.assertIn("condition: service_healthy", compose)
        self.assertEqual(compose.count("no-new-privileges:true"), 4)
        self.assertEqual(compose.count("      - ALL"), 4)
        self.assertIn("  email-worker:", compose)
        self.assertIn("  agent-worker:", compose)
        self.assertIn('command: ["python", "-m", "app.agent_worker"]', compose)
        self.assertIn('command: ["python", "-m", "app.email_worker"]', compose)
        self.assertIn("condition: service_healthy", compose.split("  email-worker:", 1)[1].split("  agent-worker:", 1)[0])
        self.assertIn("condition: service_healthy", compose.split("  agent-worker:", 1)[1].split("  clamav:", 1)[0])
        api = (ROOT / "backend" / "app" / "main.py").read_text()
        self.assertIn('@app.get("/health")', api)
        self.assertIn('@app.get("/ready")', api)
        self.assertIn('SELECT 1', api)
        self.assertIn('socket.create_connection', api)


    def test_frontend_security_headers_are_configured_for_all_routes(self):
        config = (ROOT / "frontend" / "next.config.ts").read_text()
        required_headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
            "Cross-Origin-Opener-Policy": "same-origin",
        }
        for name, value in required_headers.items():
            with self.subTest(header=name):
                self.assertIn(f"key: '{name}'", config)
                self.assertIn(f"value: '{value}'", config)
        self.assertIn("source: '/:path*'", config)
        self.assertIn("headers: securityHeaders", config)


if __name__ == "__main__":
    unittest.main()
