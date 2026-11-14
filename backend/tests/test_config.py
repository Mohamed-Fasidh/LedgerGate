import os
import subprocess
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]


def run_config_with_env(overrides):
    env = {"PATH": os.environ.get("PATH", "")}
    # Windows Python needs its OS entropy provider available during startup.
    # Keep the subprocess otherwise isolated from ambient app configuration.
    for name in ("SYSTEMROOT", "WINDIR"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", "import app.config"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def valid_s3_env(**overrides):
    env = {
        "APP_ENV": "production",
        "STORAGE_BACKEND": "s3",
        "S3_BUCKET": "ledgergate-private",
        "S3_REGION": "ap-south-1",
        "JWT_SECRET_KEY": "a-production-secret-with-at-least-32-bytes",
        "APP_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        "DATABASE_URL": "postgresql+psycopg://ledgergate:secret@db:5432/ledgergate",
        "CORS_ORIGINS": "https://ledgergate.example.com",
        "MALWARE_SCAN_HOST": "clamav",
    }
    env.update(overrides)
    return env


def test_production_s3_configuration_accepts_iam_credentials():
    result = run_config_with_env(valid_s3_env())
    assert result.returncode == 0, result.stderr


def test_s3_configuration_rejects_incomplete_static_credentials():
    result = run_config_with_env(valid_s3_env(S3_ACCESS_KEY_ID="only-access-key"))
    assert result.returncode != 0
    assert "must be configured together" in result.stderr


def test_s3_configuration_rejects_blank_region():
    result = run_config_with_env(valid_s3_env(S3_REGION=" "))
    assert result.returncode != 0
    assert "S3_REGION is required" in result.stderr


def test_production_s3_configuration_rejects_http_endpoint():
    result = run_config_with_env(valid_s3_env(S3_ENDPOINT_URL="http://object-store.internal"))
    assert result.returncode != 0
    assert "must use HTTPS" in result.stderr


def test_production_configuration_requires_malware_scanner():
    result = run_config_with_env(valid_s3_env(MALWARE_SCAN_HOST=""))
    assert result.returncode != 0
    assert "MALWARE_SCAN_HOST" in result.stderr
