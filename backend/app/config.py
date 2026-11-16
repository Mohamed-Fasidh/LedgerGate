from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "LedgerGate"
    app_env: str = "development"
    database_url: str = "sqlite:///./ledgergate.db"
    jwt_secret_key: str = "unsafe-development-secret-change-this"
    jwt_algorithm: str = "HS256"
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
    clamav_host: str = ""
    clamav_port: int = 3310
    clamav_timeout_seconds: int = 20
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self):
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

settings=Settings()
if settings.app_env.lower()=="production":
    if settings.jwt_secret_key=="unsafe-development-secret-change-this" or len(settings.jwt_secret_key)<32:
        raise RuntimeError("Production requires a unique JWT_SECRET_KEY of at least 32 characters")
    if "*" in settings.cors_origin_list:
        raise RuntimeError("Wildcard CORS is forbidden in production")
    if settings.storage_backend.lower() != "s3" or not settings.s3_bucket:
        raise RuntimeError("Production requires S3-compatible private object storage and S3_BUCKET")
    if not settings.clamav_host:
        raise RuntimeError("Production requires CLAMAV_HOST for malware scanning")

