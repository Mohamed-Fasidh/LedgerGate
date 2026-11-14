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
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_region: str = "ap-south-1"
    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_force_path_style: bool = False
    s3_presigned_url_expiry_seconds: int = 300
    max_upload_mb: int = 15
    max_upload_pdf_pages: int = 100
    dual_approval_threshold_inr: float = 100000.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()

if settings.access_token_expire_minutes < 5 or settings.access_token_expire_minutes > 60:
    raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be between 5 and 60")

if settings.storage_backend not in {"local", "s3"}:
    raise RuntimeError("STORAGE_BACKEND must be local or s3")

if settings.s3_presigned_url_expiry_seconds < 60 or settings.s3_presigned_url_expiry_seconds > 900:
    raise RuntimeError("S3_PRESIGNED_URL_EXPIRY_SECONDS must be between 60 and 900")

if settings.storage_backend == "s3":
    if not settings.s3_bucket.strip():
        raise RuntimeError("S3_BUCKET is required when STORAGE_BACKEND=s3")
    if not settings.s3_region.strip():
        raise RuntimeError("S3_REGION is required when STORAGE_BACKEND=s3")
    if bool(settings.s3_access_key_id) != bool(settings.s3_secret_access_key):
        raise RuntimeError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be configured together")

if settings.max_upload_mb < 1 or settings.max_upload_mb > 50:
    raise RuntimeError("MAX_UPLOAD_MB must be between 1 and 50")

if settings.max_upload_pdf_pages < 1 or settings.max_upload_pdf_pages > 500:
    raise RuntimeError("MAX_UPLOAD_PDF_PAGES must be between 1 and 500")

if settings.dual_approval_threshold_inr <= 0:
    raise RuntimeError("DUAL_APPROVAL_THRESHOLD_INR must be greater than zero")

if settings.jwt_algorithm != "HS256":
    raise RuntimeError("Only HS256 is supported by this deployment configuration")

if settings.app_env.lower() == "production":
    if settings.storage_backend != "s3":
        raise RuntimeError("Production requires private S3-compatible document storage")
    if settings.s3_endpoint_url and not settings.s3_endpoint_url.startswith("https://"):
        raise RuntimeError("Production S3_ENDPOINT_URL must use HTTPS")
    if settings.jwt_secret_key == "unsafe-development-secret-change-this" or len(settings.jwt_secret_key.encode("utf-8")) < 32:
        raise RuntimeError("Production requires a unique JWT_SECRET_KEY of at least 32 bytes")
    if settings.database_url.startswith("sqlite"):
        raise RuntimeError("Production requires PostgreSQL; SQLite is development-only")
    if not settings.database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("Production DATABASE_URL must use PostgreSQL with psycopg")
    if not settings.cors_origin_list or "*" in settings.cors_origin_list:
        raise RuntimeError("Production CORS_ORIGINS must contain explicit HTTPS origins")
    if any(not origin.startswith("https://") for origin in settings.cors_origin_list):
        raise RuntimeError("Production CORS origins must use HTTPS")
