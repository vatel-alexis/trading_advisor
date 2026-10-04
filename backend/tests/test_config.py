import pytest
from pydantic import ValidationError

from app.config import Settings


def test_live_broker_env_is_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(broker_env="live")


@pytest.mark.parametrize(
    ("broker", "url"),
    [("alpaca", "https://paper-api.alpaca.markets"), ("tradier", "https://sandbox.tradier.com")],
)
def test_broker_url_is_always_a_demo_endpoint(broker: str, url: str) -> None:
    assert Settings(broker=broker).broker_base_url == url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require",
        "postgres://u:p@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require",
        "postgresql+psycopg://u:p@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require",
    ],
)
def test_hosted_database_url_uses_psycopg(url: str) -> None:
    assert Settings(database_url=url).database_url == (
        "postgresql+psycopg://u:p@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require"
    )
