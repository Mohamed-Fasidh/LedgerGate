import pytest
from cryptography.fernet import Fernet

from app.config import Settings, validate_production_settings


def production_settings(**overrides):
    values = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://ledgergate_runtime:runtime-password@db.internal:5432/ledgergate",
        "jwt_secret_key": "j" * 48,
        "mfa_encryption_key": Fernet.generate_key().decode("ascii"),
        "audit_worm_bucket": "ledgergate-audit-worm",
        "audit_export_signing_key": "s" * 48,
        "audit_export_signing_key_id": "key-2026-01",
        "cors_origins": "https://app.example.com,https://admin.example.com",
        "storage_backend": "s3",
        "s3_bucket": "ledgergate-private",
        "clamav_host": "clamav.internal",
        "db_runtime_password": "r" * 48,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_accepts_explicit_https_origins_and_private_services():
    validate_production_settings(production_settings())


@pytest.mark.parametrize(
    "origin",
    [
        "http://app.example.com",
        "https://localhost:3000",
        "https://127.0.0.1:3000",
        "https://app.example.com/dashboard",
        "https://user:password@app.example.com",
        "*",
    ],
)
def test_production_rejects_insecure_or_non_origin_cors_values(origin):
    with pytest.raises(RuntimeError, match="CORS origins"):
        validate_production_settings(production_settings(cors_origins=origin))


def test_production_rejects_empty_cors_allowlist():
    with pytest.raises(RuntimeError, match="at least one explicit HTTPS"):
        validate_production_settings(production_settings(cors_origins=""))


def test_production_requires_https_for_custom_s3_endpoint():
    with pytest.raises(RuntimeError, match="S3_ENDPOINT_URL"):
        validate_production_settings(
            production_settings(s3_endpoint_url="http://object-store.internal:9000")
        )


def test_development_keeps_localhost_http_origin_supported():
    config = Settings(_env_file=None, app_env="development", cors_origins="http://localhost:3000")
    validate_production_settings(config)


def test_production_requires_explicit_audit_signing_key_id():
    with pytest.raises(RuntimeError, match="AUDIT_EXPORT_SIGNING_KEY_ID"):
        validate_production_settings(production_settings(audit_export_signing_key_id=""))
