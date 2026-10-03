from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_database_and_paper_env(migrated_db: str) -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "broker_env": "paper"}
