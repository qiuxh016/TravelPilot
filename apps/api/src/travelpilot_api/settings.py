from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    registration_invite_code: str = ""
    auth_rate_limit_hash_secret: str = ""
    login_max_attempts: int = 5
    login_window_minutes: int = 15
    login_lockout_minutes: int = 15
    public_auth_ip_limit_per_minute: int = Field(default=30, ge=1, le=10000)
    email_request_ip_limit_per_hour: int = Field(default=30, ge=1, le=10000)
    email_send_limit_per_hour: int = Field(default=100, ge=1, le=100000)
    email_action_cooldown_seconds: int = Field(default=60, ge=1, le=3600)
    advisor_destination_limit_per_user: int = Field(default=3, ge=1, le=100)
    advisor_destination_window_seconds: int = Field(default=600, ge=60, le=86400)
    web_base_url: str = "http://localhost:3001"
    web_allowed_origins: str = "http://localhost:3000,http://localhost:3001"
    session_cookie_domain: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    force_development_email_outbox: bool = False
    maps_provider: str = "fake"
    amap_api_key: str = ""
    maps_timeout_seconds: float = 8
    weather_provider: str = "open_meteo"
    weather_timeout_seconds: float = 8
    redis_url: str = "redis://localhost:6379/0"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    # Caps generated tokens across extraction and response; prompt tokens are tracked separately.
    llm_max_tokens_per_turn: int = Field(default=1200, ge=128, le=8000)
    # Per-session generated-token allowance; users must explicitly extend it.
    llm_max_output_tokens_per_session: int = Field(default=12000, ge=128, le=100000)
    media_storage_dir: str = ".data/media"
    media_storage_backend: str = "local"
    media_max_upload_bytes: int = 10_485_760
    media_s3_bucket: str = ""
    media_s3_region: str = "us-east-1"
    media_s3_endpoint_url: str = ""
    media_s3_access_key_id: str = ""
    media_s3_secret_access_key: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.web_allowed_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def require_production_invite(self):
        origins = self.cors_origins
        parsed_origins = [urlsplit(origin) for origin in origins]
        web_base = urlsplit(self.web_base_url)
        if not origins or any(
            origin == "*" or parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
            for origin, parsed in zip(origins, parsed_origins, strict=True)
        ):
            raise ValueError("WEB_ALLOWED_ORIGINS must contain exact HTTP(S) origins")
        if self.media_storage_backend.lower() not in {"local", "s3"}:
            raise ValueError("MEDIA_STORAGE_BACKEND must be local or s3")
        if self.app_env.lower() == "production" and not self.registration_invite_code:
            raise ValueError("REGISTRATION_INVITE_CODE is required in production")
        if self.app_env.lower() == "production" and len(self.auth_rate_limit_hash_secret.strip()) < 32:
            raise ValueError("AUTH_RATE_LIMIT_HASH_SECRET must contain at least 32 characters in production")
        if self.app_env.lower() == "production" and any(
            parsed.scheme != "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            for parsed in parsed_origins
        ):
            raise ValueError("WEB_ALLOWED_ORIGINS must be configured for production")
        if self.app_env.lower() == "production" and (
            web_base.scheme != "https" or not web_base.netloc or web_base.username or web_base.password
        ):
            raise ValueError("WEB_BASE_URL must be an HTTPS URL in production")
        if self.app_env.lower() == "production" and not all(
            (self.smtp_host, self.smtp_from_email)
        ):
            raise ValueError("SMTP_HOST and SMTP_FROM_EMAIL are required in production")
        if self.app_env.lower() == "production" and self.force_development_email_outbox:
            raise ValueError("FORCE_DEVELOPMENT_EMAIL_OUTBOX cannot be enabled in production")
        if self.app_env.lower() == "production" and self.media_storage_backend.lower() != "s3":
            raise ValueError("MEDIA_STORAGE_BACKEND=s3 is required in production")
        if self.media_storage_backend.lower() == "s3" and not self.media_s3_bucket:
            raise ValueError("MEDIA_S3_BUCKET is required when MEDIA_STORAGE_BACKEND=s3")
        return self


settings = Settings()
