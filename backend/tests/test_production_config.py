import pytest
from cryptography.fernet import Fernet

from app.config import Settings, validate_production_settings


def production_settings(**overrides):
    values = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://ledgergate_runtime:runtime-password@db.internal:5432/ledgergate?sslmode=verify-full&sslrootcert=%2Frun%2Fsecrets%2Fpostgres-ca.crt",
        "jwt_secret_key": "j" * 48,
        "mfa_encryption_key": Fernet.generate_key().decode("ascii"),
        "email_outbox_encryption_key": Fernet.generate_key().decode("ascii"),
        "audit_worm_bucket": "ledgergate-audit-worm",
        "audit_export_signing_key": "s" * 48,
        "audit_export_signing_key_id": "key-2026-01",
        "cors_origins": "https://app.example.com,https://admin.example.com",
        "storage_backend": "s3",
        "s3_bucket": "ledgergate-private",
        "clamav_host": "clamav.internal",
        "db_runtime_password": "r" * 48,
        "email_smtp_host": "smtp.example.com",
        "email_smtp_port": 587,
        "email_smtp_username": "ledgergate@example.com",
        "email_smtp_password": "smtp-secret",
        "email_from_address": "LedgerGate <no-reply@example.com>",
        "public_app_url": "https://app.example.com",
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


@pytest.mark.parametrize("algorithm", ["none", "RS256", "HS512"])
def test_production_rejects_unapproved_jwt_algorithms(algorithm):
    with pytest.raises(RuntimeError, match="JWT_ALGORITHM must be HS256"):
        validate_production_settings(production_settings(jwt_algorithm=algorithm))


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"clamav_port": 0}, "CLAMAV_PORT"),
        ({"clamav_port": 65536}, "CLAMAV_PORT"),
        ({"clamav_timeout_seconds": 0}, "CLAMAV_TIMEOUT_SECONDS"),
        ({"clamav_timeout_seconds": 121}, "CLAMAV_TIMEOUT_SECONDS"),
        ({"max_upload_mb": 0}, "MAX_UPLOAD_MB"),
        ({"max_upload_mb": 101}, "MAX_UPLOAD_MB"),
        ({"access_token_expire_minutes": 0}, "ACCESS_TOKEN_EXPIRE_MINUTES"),
        ({"access_token_expire_minutes": 61}, "ACCESS_TOKEN_EXPIRE_MINUTES"),
    ],
)
def test_production_rejects_unsafe_resource_and_token_limits(overrides, message):
    with pytest.raises(RuntimeError, match=message):
        validate_production_settings(production_settings(**overrides))



def test_production_requires_authenticated_smtp_for_account_recovery():
    with pytest.raises(RuntimeError, match="authenticated SMTP"):
        validate_production_settings(production_settings(email_smtp_password=""))


def test_production_requires_starttls_smtp_port():
    with pytest.raises(RuntimeError, match="port 587"):
        validate_production_settings(production_settings(email_smtp_port=25))


def test_production_requires_https_public_app_origin():
    with pytest.raises(RuntimeError, match="PUBLIC_APP_URL"):
        validate_production_settings(production_settings(public_app_url="http://app.example.com"))



def test_production_requires_dedicated_valid_email_outbox_encryption_key():
    with pytest.raises(RuntimeError, match="EMAIL_OUTBOX_ENCRYPTION_KEY"):
        validate_production_settings(production_settings(email_outbox_encryption_key=""))
    with pytest.raises(RuntimeError, match="valid Fernet key"):
        validate_production_settings(production_settings(email_outbox_encryption_key="not-a-fernet-key"))


def test_production_requires_verified_postgresql_tls():
    with pytest.raises(RuntimeError, match="sslmode=verify-full"):
        validate_production_settings(
            production_settings(database_url="postgresql+psycopg://ledgergate_runtime:runtime-password@db.internal:5432/ledgergate?sslmode=require&sslrootcert=/run/secrets/postgres-ca.crt")
        )
    with pytest.raises(RuntimeError, match="sslrootcert"):
        validate_production_settings(
            production_settings(database_url="postgresql+psycopg://ledgergate_runtime:runtime-password@db.internal:5432/ledgergate?sslmode=verify-full")
        )


@pytest.mark.parametrize(
    "public_url",
    [
        "https://app.example.com/dashboard",
        "https://app.example.com/path/",
        "https://localhost:3000",
        "https://127.0.0.1:8000",
        "https://user:password@app.example.com",
        "https://app.example.com?next=/login",
        "https://app.example.com#fragment",
        "https://app.example.com:invalid",
        "https://app.example.com:70000",
    ],
)
def test_production_rejects_non_origin_public_app_urls(public_url):
    with pytest.raises(RuntimeError, match="PUBLIC_APP_URL"):
        validate_production_settings(production_settings(public_app_url=public_url))


def test_production_allows_trailing_slash_on_public_app_origin():
    validate_production_settings(production_settings(public_app_url="https://app.example.com/"))


@pytest.mark.parametrize(
    "from_address",
    [
        "",
        "not-an-email",
        "missing-domain@",
        "@example.com",
        "two@@example.com",
        "no-reply@example.com\\nBcc: attacker@example.com",
        "no-reply@example.com, attacker@example.com",
        "LedgerGate <not-an-email>",
        "LedgerGate <no-reply@example.com> trailing",
        "no-reply@localhost",
    ],
)
def test_production_rejects_malformed_or_injected_email_from_addresses(from_address):
    with pytest.raises(RuntimeError, match="EMAIL_FROM_ADDRESS"):
        validate_production_settings(production_settings(email_from_address=from_address))


def test_production_accepts_plain_email_from_address():
    validate_production_settings(production_settings(email_from_address="no-reply@example.com"))


@pytest.mark.parametrize(
    "origin",
    [
        "https://app.example.com:invalid",
        "https://app.example.com:70000",
        " https://app.example.com",
        "https://app.example.com\n",
        "https://",
    ],
)
def test_production_rejects_malformed_cors_origins(origin):
    with pytest.raises(RuntimeError, match="CORS origins"):
        validate_production_settings(production_settings(cors_origins=origin))
