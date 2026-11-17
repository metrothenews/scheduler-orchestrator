from fastapi.testclient import TestClient

from scheduler_orchestrator.service import app

client = TestClient(app)
BODY = {"command": "scheduler-orchestrator-run"}


def _env(monkeypatch, key=None):
    monkeypatch.setenv("SHEETS_JSON_URL", "https://example.invalid")
    monkeypatch.delenv("ORCHESTRATOR_JOB_NAME", raising=False)
    if key is None:
        monkeypatch.delenv("TRIGGER_API_KEY", raising=False)
    else:
        monkeypatch.setenv("TRIGGER_API_KEY", key)


def test_key_configured_rejects_missing_or_wrong_key(monkeypatch):
    _env(monkeypatch, "s3cret")
    assert client.post("/v1/run", json=BODY).status_code == 401
    assert client.post("/v1/run", json=BODY, headers={"X-API-Key": "nope"}).status_code == 401


def test_correct_key_or_no_key_configured_passes_auth(monkeypatch):
    # Past auth, the request stops at the missing ORCHESTRATOR_JOB_NAME check (500), not 401.
    _env(monkeypatch, "s3cret")
    assert client.post("/v1/run", json=BODY, headers={"X-API-Key": "s3cret"}).status_code == 500
    _env(monkeypatch, None)
    assert client.post("/v1/run", json=BODY).status_code == 500
