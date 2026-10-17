import pytest
from pydantic import ValidationError
from travelpilot_api.settings import Settings


def test_web_allowed_origins_accepts_only_exact_http_origins():
    settings = Settings(
        app_env="development",
        web_allowed_origins=" http://localhost:3000,https://travel.example.com ",
    )

    assert settings.cors_origins == [
        "http://localhost:3000",
        "https://travel.example.com",
    ]

    with pytest.raises(ValidationError):
        Settings(app_env="development", web_allowed_origins="*")
    with pytest.raises(ValidationError):
        Settings(app_env="development", web_allowed_origins="https://travel.example.com/app")


def test_production_requires_https_frontend_and_mail_link_urls():
    base = {
        "app_env": "production",
        "registration_invite_code": "closed-trial",
        "auth_rate_limit_hash_secret": "test-only-production-secret-at-least-32",
        "smtp_host": "smtp.example.com",
        "smtp_from_email": "travel@example.com",
        "media_storage_backend": "s3",
        "media_s3_bucket": "travel-media",
        "web_allowed_origins": "http://localhost:3000",
        "web_base_url": "http://localhost:3001",
    }

    with pytest.raises(ValidationError):
        Settings(**base)

    settings = Settings(**{
        **base,
        "web_allowed_origins": "https://travel.example.com",
        "web_base_url": "https://travel.example.com",
    })
    assert settings.cors_origins == ["https://travel.example.com"]


def test_email_action_cooldown_is_bounded():
    with pytest.raises(ValidationError):
        Settings(app_env="development", email_action_cooldown_seconds=0)
    with pytest.raises(ValidationError):
        Settings(app_env="development", email_action_cooldown_seconds=3601)
