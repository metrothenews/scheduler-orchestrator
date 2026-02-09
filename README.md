# scheduler-orchestrator

Cloud Run **Service + Job**:

- **Service**: `POST /v1/run` (fast trigger) -> starts a Cloud Run **Job** execution and returns immediately.
- **Job**: long-running loop: reads `Script View` via **sheets-json**, schedules per-platform via **post-executor**, writes back status, and sleeps between rows.

This repo intentionally **never talks to the Google Sheets API** directly.

## Environment variables (bootstrap)

### Required (Service + Job)

- `SHEETS_JSON_URL` (example: `https://YOUR-SHEETS-JSON-GATEWAY.a.run.app`)
- `SCRIPT_VIEW_SHEET_ID` (default in code: `YOUR_GOOGLE_SHEET_ID`)
- `SCRIPT_VIEW_TAB_NAME` (default: `Script View`)

### Optional

- `SHEETS_JSON_API_KEY` (if you want unredacted reads from sheets-json)
- `CONFIG_RANGE_A1` (default: `A1:B30`)
- `POSTS_RANGE_A1` (default: `A40:Z1000`)
- `LOCK_RANGE_A1` (default: `D1:E1`)
- `LOCK_TTL_SECONDS` (default: `7200`)

### Service-only (to trigger the job)

- `ORCHESTRATOR_JOB_NAME` (Cloud Run Job name, e.g. `scheduler-orchestrator-job`)
- `GCP_REGION` (default: `us-east1`)
- `GCP_PROJECT` (optional; falls back to `GOOGLE_CLOUD_PROJECT`)

## Script View contract (expected keys in config block)

The job reads `CONFIG_RANGE_A1` as two columns: `key`, `value`.

Important keys (recommended):

- `post_executor_url`
- `post_executor_api_key`   (masked/unmasked handled by sheets-json + `SHEETS_JSON_API_KEY`)
- `drive_proxy_prefix`
- `timezone`
- `delay_base_seconds`
- `delay_jitter_seconds_min`
- `delay_jitter_seconds_max`
- `schedule_jitter_minutes_min`
- `schedule_jitter_minutes_max`
- `schedule_jitter_seconds_min`
- `schedule_jitter_seconds_max`

## Deploy (typical)

### 1) Deploy the Job

Example (from source):

```bash
gcloud run jobs deploy scheduler-orchestrator-job \
  --source . \
  --region us-east1 \
  --command python \
  --args -m,scheduler_orchestrator.job \
  --set-env-vars SHEETS_JSON_URL="https://YOUR-SHEETS-JSON-GATEWAY.a.run.app" \
  --set-env-vars SHEETS_JSON_API_KEY="REDACTED" \
  --set-env-vars SCRIPT_VIEW_SHEET_ID="YOUR_GOOGLE_SHEET_ID" \
  --set-env-vars SCRIPT_VIEW_TAB_NAME="Script View"
```

### 2) Deploy the Service

```bash
gcloud run deploy scheduler-orchestrator \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --set-env-vars ORCHESTRATOR_JOB_NAME="scheduler-orchestrator-job" \
  --set-env-vars SHEETS_JSON_URL="https://YOUR-SHEETS-JSON-GATEWAY.a.run.app" \
  --set-env-vars SHEETS_JSON_API_KEY="REDACTED"
```

### 3) Allow the Service's runtime identity to execute the Job

`roles/run.invoker` can execute Cloud Run jobs (permission `run.jobs.run`). You can also use `roles/run.jobsExecutor` if you want a job-specific role. (See Cloud Run IAM roles docs.) 

In practice: grant the **service's runtime service account** permission on the job.

```bash
SERVICE_SA="YOUR_SERVICE_RUNTIME_SA@YOUR_PROJECT.iam.gserviceaccount.com"

gcloud run jobs add-iam-policy-binding scheduler-orchestrator-job \
  --region us-east1 \
  --member "serviceAccount:${SERVICE_SA}" \
  --role "roles/run.invoker"
```

## Trigger

```bash
curl -sS -X POST "https://YOUR_SERVICE_URL/v1/run" \
  -H "Content-Type: application/json" \
  -d '{"command":"scheduler-orchestrator-run"}'
```

## Notes

- The job implements **deterministic** per-platform schedule jitter based on `(post_id, platform, base_time)`.
- The job also implements a **sheet lock** so two triggers can't process the same rows concurrently.
