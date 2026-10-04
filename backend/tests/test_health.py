from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_database_and_paper_env(migrated_db: str) -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "broker_env": "paper"}


def test_api_token_guards_everything_but_health(migrated_db: str, monkeypatch) -> None:
    from app.main import settings

    monkeypatch.setattr(settings, "api_token", "s3cret")
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/dashboard").status_code == 401
    assert client.get("/dashboard", headers={"X-API-Token": "wrong"}).status_code == 401
    assert client.get("/dashboard", headers={"X-API-Token": "s3cret"}).status_code == 200
