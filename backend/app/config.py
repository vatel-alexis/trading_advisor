from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Only demo endpoints are allowed. Pointing the app at a live account requires a code change,
# never just an environment variable.
PAPER_BROKER_URLS: dict[str, str] = {
    "alpaca": "https://paper-api.alpaca.markets",
    "tradier": "https://sandbox.tradier.com",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://trading:trading@localhost:5432/trading_advisor"
    broker: Literal["alpaca", "tradier"] = "alpaca"
    broker_env: Literal["paper"] = "paper"
    broker_api_key: str = ""
    broker_api_secret: str = ""
    starting_capital: int = 20_000
    cors_origins: list[str] = ["http://localhost:3000"]
    # Shared secret between the site and a publicly hosted API; empty = no check (local stack).
    api_token: str = ""

    @field_validator("database_url")
    @classmethod
    def psycopg_driver(cls, value: str) -> str:
        """Accept the plain URL hosted Postgres providers (Neon...) hand out."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value.removeprefix(prefix)
        return value

    @field_validator("broker_env", mode="before")
    @classmethod
    def paper_only(cls, value: str) -> str:
        if value != "paper":
            raise ValueError("Seul l'environnement 'paper' est autorisé.")
        return value

    @property
    def broker_base_url(self) -> str:
        return PAPER_BROKER_URLS[self.broker]


@lru_cache
def get_settings() -> Settings:
    return Settings()
