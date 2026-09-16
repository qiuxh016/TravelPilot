from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    maps_provider: str = "fake"
    amap_api_key: str = ""
    maps_timeout_seconds: float = 8
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
settings = Settings()