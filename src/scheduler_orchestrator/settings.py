from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Bootstrap
    SHEETS_JSON_URL: str
    SHEETS_JSON_API_KEY: str | None = None

    SCRIPT_VIEW_SHEET_ID: str = "YOUR_GOOGLE_SHEET_ID"
    SCRIPT_VIEW_TAB_NAME: str = "Script View"

    CONFIG_RANGE_A1: str = "A1:B30"
    POSTS_RANGE_A1: str = "A40:Z1000"
    LOCK_RANGE_A1: str = "D1:E1"

    LOCK_TTL_SECONDS: int = 7200  # 2 hours

    # Service trigger -> Job run
    ORCHESTRATOR_JOB_NAME: str | None = None
    GCP_REGION: str = "us-east1"
    GCP_PROJECT: str | None = None

    # Optional shared key for POST /v1/run (sent as X-API-Key). Use this or Cloud Run IAM.
    TRIGGER_API_KEY: str | None = None
