from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    maps_provider: str = "fake"
    amap_api_key: str = ""
    maps_timeout_seconds: float = 8
    redis_url: str = "redis://localhost:6379/0"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
settings = Settings()