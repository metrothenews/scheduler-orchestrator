from __future__ import annotations

import hmac
import os
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

import google.auth
from google.auth.transport.requests import Request

from .settings import Settings

app = FastAPI(title="scheduler-orchestrator", version="0.1.0")


class RunRequest(BaseModel):
    command: str


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"ok": "true"}


def _project_id(settings: Settings) -> str:
    return settings.GCP_PROJECT or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT") or ""


@app.post("/v1/run")
def run(req: RunRequest, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    if req.command != "scheduler-orchestrator-run":
        raise HTTPException(status_code=400, detail="Invalid command")

    settings = Settings()

    expected = (settings.TRIGGER_API_KEY or "").strip()
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")

    if not settings.ORCHESTRATOR_JOB_NAME:
        raise HTTPException(status_code=500, detail="Missing ORCHESTRATOR_JOB_NAME env var")

    project = _project_id(settings)
    if not project:
        raise HTTPException(status_code=500, detail="Could not resolve project id (set GCP_PROJECT or GOOGLE_CLOUD_PROJECT)")

    region = settings.GCP_REGION
    job_name = settings.ORCHESTRATOR_JOB_NAME

    # Cloud Run Jobs v2: POST .../jobs/{job}:run
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job_name}:run"

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    token = creds.token
    if not token:
        raise HTTPException(status_code=500, detail="Failed to obtain access token")

    with httpx.Client(timeout=30.0) as c:
        r = c.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={},
        )
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Failed to start job: {r.status_code} {r.text[:400]}")
        data = r.json()
        return {"ok": True, "job_execution": data}
