from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./footscore-dev.db"
    api_football_key: str | None = None
    api_football_base_url: str = "https://v3.football.api-sports.io"
    provider_daily_limit: int = 100
    provider_reserve: int = 10
    redis_url: str | None = None
    sync_leagues: str = ""
    sync_interval_minutes: int = 360
    jwt_secret: SecretStr | None = None
    access_token_minutes: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
