from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    registration_invite_code: str = ""
    login_max_attempts: int = 5
    login_window_minutes: int = 15
    login_lockout_minutes: int = 15
    web_base_url: str = "http://localhost:3001"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_tls: bool = True
    maps_provider: str = "fake"
    amap_api_key: str = ""
    maps_timeout_seconds: float = 8
    redis_url: str = "redis://localhost:6379/0"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    media_storage_dir: str = ".data/media"
    media_storage_backend: str = "local"
    media_max_upload_bytes: int = 10_485_760
    media_s3_bucket: str = ""
    media_s3_region: str = "us-east-1"
    media_s3_endpoint_url: str = ""
    media_s3_access_key_id: str = ""
    media_s3_secret_access_key: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def require_production_invite(self):
        if self.media_storage_backend.lower() not in {"local", "s3"}:
            raise ValueError("MEDIA_STORAGE_BACKEND must be local or s3")
        if self.app_env.lower() == "production" and not self.registration_invite_code:
            raise ValueError("REGISTRATION_INVITE_CODE is required in production")
        if self.app_env.lower() == "production" and not all(
            (self.smtp_host, self.smtp_from_email)
        ):
            raise ValueError("SMTP_HOST and SMTP_FROM_EMAIL are required in production")
        if self.app_env.lower() == "production" and self.media_storage_backend.lower() != "s3":
            raise ValueError("MEDIA_STORAGE_BACKEND=s3 is required in production")
        if self.media_storage_backend.lower() == "s3" and not self.media_s3_bucket:
            raise ValueError("MEDIA_S3_BUCKET is required when MEDIA_STORAGE_BACKEND=s3")
        return self


settings = Settings()
