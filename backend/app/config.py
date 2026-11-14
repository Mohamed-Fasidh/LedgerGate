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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()

if settings.access_token_expire_minutes < 5 or settings.access_token_expire_minutes > 60:
    raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES must be between 5 and 60")

if settings.max_upload_mb < 1 or settings.max_upload_mb > 50:
    raise RuntimeError("MAX_UPLOAD_MB must be between 1 and 50")

if settings.jwt_algorithm != "HS256":
    raise RuntimeError("Only HS256 is supported by this deployment configuration")

if settings.app_env.lower() == "production":
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
