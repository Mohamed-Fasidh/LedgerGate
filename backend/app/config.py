from pydantic_settings import BaseSettings, SettingsConfigDict
from urllib.parse import urlparse

class Settings(BaseSettings):
    app_name: str = "LedgerGate"
    app_env: str = "development"
    database_url: str = "sqlite:///./ledgergate.db"
    jwt_secret_key: str = "unsafe-development-secret-change-this"
    jwt_algorithm: str = "HS256"
    mfa_encryption_key: str = ""
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

settings=Settings()
if settings.app_env.lower()=="production":
    if settings.mfa_encryption_key:
        from cryptography.fernet import Fernet
        try:
            Fernet(settings.mfa_encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("MFA_ENCRYPTION_KEY must be a valid Fernet key") from exc
    else:
        raise RuntimeError("Production requires MFA_ENCRYPTION_KEY for encrypted authenticator secrets")
    if settings.jwt_secret_key=="unsafe-development-secret-change-this" or len(settings.jwt_secret_key)<32:
        raise RuntimeError("Production requires a unique JWT_SECRET_KEY of at least 32 characters")
    if "*" in settings.cors_origin_list:
        raise RuntimeError("Wildcard CORS is forbidden in production")
    if settings.storage_backend.lower() != "s3" or not settings.s3_bucket:
        raise RuntimeError("Production requires S3-compatible private object storage and S3_BUCKET")
    if not settings.clamav_host:
        raise RuntimeError("Production requires CLAMAV_HOST for malware scanning")
    if len(settings.db_runtime_password) < 32:
        raise RuntimeError("Production requires DB_RUNTIME_PASSWORD with at least 32 characters")
    database_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed_database_url = urlparse(database_url)
    if parsed_database_url.scheme not in ("postgres", "postgresql") or parsed_database_url.username != "ledgergate_runtime":
        raise RuntimeError("Production DATABASE_URL must use the restricted ledgergate_runtime PostgreSQL role")

