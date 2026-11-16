from pydantic_settings import BaseSettings, SettingsConfigDict
from urllib.parse import urlparse


class Settings(BaseSettings):
    app_name: str = "LedgerGate"
    app_env: str = "development"
    database_url: str = "sqlite:///./ledgergate.db"
    jwt_secret_key: str = "unsafe-development-secret-change-this"
    jwt_algorithm: str = "HS256"
    mfa_encryption_key: str = ""
    audit_worm_bucket: str = ""
    audit_worm_retention_days: int = 2555
    audit_export_signing_key: str = ""
    audit_export_signing_key_id: str = ""
    access_token_expire_minutes: int = 30
    cors_origins: str = "http://localhost:3000"
    upload_dir: str = "./uploads"
    max_upload_mb: int = 15
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_region: str = "ap-south-1"
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    db_runtime_password: str = ""
    clamav_host: str = ""
    clamav_port: int = 3310
    clamav_timeout_seconds: int = 20
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self):
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


def validate_production_settings(config: Settings) -> None:
    """Fail startup when production security configuration is unsafe or incomplete."""
    if config.app_env.lower() != "production":
        return

    if config.mfa_encryption_key:
        from cryptography.fernet import Fernet
        try:
            Fernet(config.mfa_encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("MFA_ENCRYPTION_KEY must be a valid Fernet key") from exc
    else:
        raise RuntimeError("Production requires MFA_ENCRYPTION_KEY for encrypted authenticator secrets")

    if not config.audit_worm_bucket:
        raise RuntimeError("Production requires AUDIT_WORM_BUCKET with S3 Object Lock enabled")
    if len(config.audit_export_signing_key) < 32:
        raise RuntimeError("Production requires AUDIT_EXPORT_SIGNING_KEY with at least 32 characters")
    if not config.audit_export_signing_key_id.strip():
        raise RuntimeError("Production requires AUDIT_EXPORT_SIGNING_KEY_ID")
    if config.audit_worm_retention_days < 1 or config.audit_worm_retention_days > 3650:
        raise RuntimeError("AUDIT_WORM_RETENTION_DAYS must be between 1 and 3650")
    if config.jwt_algorithm != "HS256":
        raise RuntimeError("Production JWT_ALGORITHM must be HS256; asymmetric or unsecured algorithms are not supported")
    if config.jwt_secret_key == "unsafe-development-secret-change-this" or len(config.jwt_secret_key) < 32:
        raise RuntimeError("Production requires a unique JWT_SECRET_KEY of at least 32 characters")

    origins = config.cors_origin_list
    if not origins:
        raise RuntimeError("Production requires at least one explicit HTTPS CORS origin")
    for origin in origins:
        parsed = urlparse(origin)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise RuntimeError("Production CORS origins must be explicit HTTPS origins without paths or localhost")

    if config.storage_backend.lower() != "s3" or not config.s3_bucket:
        raise RuntimeError("Production requires S3-compatible private object storage and S3_BUCKET")
    if config.s3_endpoint_url:
        endpoint = urlparse(config.s3_endpoint_url)
        if endpoint.scheme != "https" or not endpoint.netloc or endpoint.username or endpoint.password:
            raise RuntimeError("Production S3_ENDPOINT_URL must use HTTPS without embedded credentials")
    if not config.clamav_host:
        raise RuntimeError("Production requires CLAMAV_HOST for malware scanning")
    if len(config.db_runtime_password) < 32:
        raise RuntimeError("Production requires DB_RUNTIME_PASSWORD with at least 32 characters")
    database_url = config.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed_database_url = urlparse(database_url)
    if parsed_database_url.scheme not in ("postgres", "postgresql") or parsed_database_url.username != "ledgergate_runtime":
        raise RuntimeError("Production DATABASE_URL must use the restricted ledgergate_runtime PostgreSQL role")


settings = Settings()
validate_production_settings(settings)
