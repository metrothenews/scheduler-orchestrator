from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from dateutil import parser as dtparser
from zoneinfo import ZoneInfo

from .jitter import JitterSpec, deterministic_jitter
from .post_executor_client import PostExecutorClient
from .settings import Settings
from .sheets_json_client import SheetsJsonClient
from .utils import cell_a1, normalize_header, parse_scalar, truthy


PLATFORMS = ["youtube", "instagram", "tiktok", "facebook", "x", "threads"]

PLATFORM_WRITEBACK = {
    "youtube": {"job_id": "yt_job_id", "scheduled": "yt_scheduled_utc"},
    "instagram": {"job_id": "ig_job_id", "scheduled": "ig_scheduled_utc"},
    "tiktok": {"job_id": "tt_job_id", "scheduled": "tt_scheduled_utc"},
    "facebook": {"job_id": "fb_job_id", "scheduled": "fb_scheduled_utc"},
    "x": {"job_id": "x_job_id", "scheduled": "x_scheduled_utc"},
    "threads": {"job_id": "threads_job_id", "scheduled": "threads_scheduled_utc"},
}


@dataclass
class RuntimeConfig:
    post_executor_url: str
    post_executor_api_key: str
    drive_proxy_prefix: str
    timezone: str = "America/New_York"

    delay_base_seconds: int = 300
    delay_jitter_seconds_min: int = 1
    delay_jitter_seconds_max: int = 120

    jitter: JitterSpec = JitterSpec()

    lock_ttl_seconds: int = 7200


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_local_dt(s: str, tz_name: str) -> datetime:
    # Preferred: YYYY-MM-DD HH:MM:SS (naive)
    s = str(s).strip()
    tz = ZoneInfo(tz_name)
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=tz)
    except Exception:
        dt = dtparser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)
        return dt.replace(microsecond=0)


def _to_rfc3339_z(dt_utc: datetime) -> str:
    dt_utc = dt_utc.astimezone(timezone.utc).replace(microsecond=0)
    return dt_utc.isoformat().replace("+00:00", "Z")


def _build_config(kv_rows: List[List[Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for row in kv_rows:
        if not row or len(row) < 2:
            continue
        k = str(row[0]).strip()
        if not k:
            continue
        out[k] = parse_scalar(row[1])
    return out


def _get_cfg(cfg: Dict[str, Any], key: str, default: Any) -> Any:
    v = cfg.get(key, default)
    return default if v is None or v == "" else v


def _acquire_lock(sheets: SheetsJsonClient, settings: Settings) -> Tuple[str, bool]:
    token = str(uuid.uuid4())
    now = _now_utc().isoformat().replace("+00:00", "Z")

    # Read lock
    vals = sheets.read_range(
        sheet_id=settings.SCRIPT_VIEW_SHEET_ID,
        tab_name=settings.SCRIPT_VIEW_TAB_NAME,
        a1_range=settings.LOCK_RANGE_A1,
        unredacted=False,
    )
    existing_token = (vals[0][0] if vals and vals[0] and len(vals[0]) > 0 else "") if isinstance(vals, list) else ""
    existing_ts = (vals[0][1] if vals and vals[0] and len(vals[0]) > 1 else "") if isinstance(vals, list) else ""

    if existing_token and existing_ts:
        try:
            # Assume stored as RFC3339 or parseable
            ts = dtparser.parse(str(existing_ts))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (_now_utc() - ts.astimezone(timezone.utc)).total_seconds()
            if age < settings.LOCK_TTL_SECONDS:
                return token, False
        except Exception:
            # If we can't parse timestamp, treat as locked (safe)
            return token, False

    # Write lock token + timestamp
    sheets.write_range(
        sheet_id=settings.SCRIPT_VIEW_SHEET_ID,
        tab_name=settings.SCRIPT_VIEW_TAB_NAME,
        a1_range=settings.LOCK_RANGE_A1,
        values=[[token, now]],
        unredacted=False,
    )
    return token, True


def _release_lock(sheets: SheetsJsonClient, settings: Settings, token: str) -> None:
    # Best-effort: only clear if we still own it
    try:
        vals = sheets.read_range(
            sheet_id=settings.SCRIPT_VIEW_SHEET_ID,
            tab_name=settings.SCRIPT_VIEW_TAB_NAME,
            a1_range=settings.LOCK_RANGE_A1,
            unredacted=False,
        )
        existing_token = (vals[0][0] if vals and vals[0] and len(vals[0]) > 0 else "") if isinstance(vals, list) else ""
        if str(existing_token).strip() != token:
            return
        sheets.write_range(
            sheet_id=settings.SCRIPT_VIEW_SHEET_ID,
            tab_name=settings.SCRIPT_VIEW_TAB_NAME,
            a1_range=settings.LOCK_RANGE_A1,
            values=[["", ""]],
            unredacted=False,
        )
    except Exception:
        return


def _find_col(headers: List[str], name: str) -> int | None:
    n = normalize_header(name)
    for i, h in enumerate(headers):
        if normalize_header(h) == n:
            return i
    return None


def main() -> None:
    settings = Settings()

    sheets = SheetsJsonClient(base_url=settings.SHEETS_JSON_URL, api_key=settings.SHEETS_JSON_API_KEY)

    # Lock
    lock_token, ok = _acquire_lock(sheets, settings)
    if not ok:
        print("Lock is held. Aborting.")
        return

    try:
        # Read config block
        cfg_rows = sheets.read_range(
            sheet_id=settings.SCRIPT_VIEW_SHEET_ID,
            tab_name=settings.SCRIPT_VIEW_TAB_NAME,
            a1_range=settings.CONFIG_RANGE_A1,
            unredacted=True,
        )
        cfg = _build_config(cfg_rows)

        runtime = RuntimeConfig(
            post_executor_url=str(_get_cfg(cfg, "post_executor_url", "")).strip(),
            post_executor_api_key=str(_get_cfg(cfg, "post_executor_api_key", "")).strip(),
            drive_proxy_prefix=str(_get_cfg(cfg, "drive_proxy_prefix", "")).strip(),
            timezone=str(_get_cfg(cfg, "timezone", "America/New_York")).strip(),
            delay_base_seconds=int(_get_cfg(cfg, "delay_base_seconds", 300)),
            delay_jitter_seconds_min=int(_get_cfg(cfg, "delay_jitter_seconds_min", 1)),
            delay_jitter_seconds_max=int(_get_cfg(cfg, "delay_jitter_seconds_max", 120)),
            jitter=JitterSpec(
                minutes_min=int(_get_cfg(cfg, "schedule_jitter_minutes_min", 1)),
                minutes_max=int(_get_cfg(cfg, "schedule_jitter_minutes_max", 20)),
                seconds_min=int(_get_cfg(cfg, "schedule_jitter_seconds_min", 5)),
                seconds_max=int(_get_cfg(cfg, "schedule_jitter_seconds_max", 55)),
            ),
            lock_ttl_seconds=int(_get_cfg(cfg, "lock_ttl_seconds", settings.LOCK_TTL_SECONDS)),
        )

        if not runtime.post_executor_url or not runtime.post_executor_api_key or not runtime.drive_proxy_prefix:
            raise RuntimeError("Missing required Script View config keys: post_executor_url, post_executor_api_key, drive_proxy_prefix")

        post_exec = PostExecutorClient(base_url=runtime.post_executor_url, api_key=runtime.post_executor_api_key)

        # Read posts table (includes headers at first row of range)
        table = sheets.read_range(
            sheet_id=settings.SCRIPT_VIEW_SHEET_ID,
            tab_name=settings.SCRIPT_VIEW_TAB_NAME,
            a1_range=settings.POSTS_RANGE_A1,
            unredacted=False,
        )
        if not table or len(table) < 2:
            print("No rows found.")
            return

        headers = [str(h or "").strip() for h in table[0]]
        rows = table[1:]

        # Resolve required columns
        col_post_id = _find_col(headers, "post_id")
        col_drive = _find_col(headers, "drive_file_id")
        col_caption = _find_col(headers, "caption")
        col_sched = _find_col(headers, "scheduled_local")
        col_status = _find_col(headers, "status")
        col_last_run = _find_col(headers, "last_run_at")
        col_last_err = _find_col(headers, "last_error")

        if col_drive is None or col_caption is None or col_sched is None:
            raise RuntimeError("Posts table missing required headers: drive_file_id, caption, scheduled_local")

        # For writebacks, we need absolute row numbers.
        # POST range starts at row number parsed from A1 (e.g. A40:Z1000 -> 40)
        m = __import__("re").match(r"^[A-Z]+(\d+):[A-Z]+\d+$", settings.POSTS_RANGE_A1.strip(), flags=__import__("re").I)
        start_row = int(m.group(1)) if m else 40
        header_row_num = start_row
        first_data_row_num = header_row_num + 1

        for i, r in enumerate(rows):
            row_num = first_data_row_num + i

            # Extract cells safely
            def cell(idx: int | None) -> Any:
                if idx is None:
                    return ""
                return r[idx] if idx < len(r) else ""

            status = str(cell(col_status)).strip().upper() if col_status is not None else ""
            if status and status not in ("", "READY"):
                continue

            drive_file_id = str(cell(col_drive)).strip()
            caption = str(cell(col_caption)).strip()
            scheduled_local = str(cell(col_sched)).strip()
            if not drive_file_id or not caption or not scheduled_local:
                continue

            # Determine platform toggles
            selected = []
            for p in PLATFORMS:
                ci = _find_col(headers, p)
                if ci is not None and truthy(cell(ci)):
                    selected.append(p)
            if not selected:
                continue

            post_id = str(cell(col_post_id)).strip() if col_post_id is not None else ""
            if not post_id:
                post_id = f"row{row_num}"

            base_local_dt = _parse_local_dt(scheduled_local, runtime.timezone)

            errors: List[str] = []
            successes = 0

            for platform in selected:
                # Skip if already has job_id
                wb = PLATFORM_WRITEBACK.get(platform, {})
                job_col = _find_col(headers, wb.get("job_id", ""))
                if job_col is not None and str(cell(job_col)).strip():
                    continue

                minutes, seconds = deterministic_jitter(f"{post_id}|{platform}|{base_local_dt.isoformat()}", runtime.jitter)
                dt_local = base_local_dt + timedelta(minutes=minutes, seconds=seconds)
                dt_utc = dt_local.astimezone(timezone.utc)

                # Guard: don't schedule in the past
                if dt_utc < _now_utc() + timedelta(seconds=60):
                    errors.append(f"{platform}: scheduled time is in the past/too soon ({_to_rfc3339_z(dt_utc)})")
                    continue

                accounts = {k: False for k in PLATFORMS}
                accounts[platform] = True

                payload = {
                    "caption": caption,
                    "scheduled_send_time": _to_rfc3339_z(dt_utc),
                    "media_source_url": f"{runtime.drive_proxy_prefix}{drive_file_id}",
                    "accounts": accounts,
                    "title": None,
                    "youtube_privacy": "public",
                }

                try:
                    resp = post_exec.schedule(payload)
                    successes += 1

                    # Write platform writebacks (job_id + scheduled_utc)
                    job_id = resp.get("job_id") or resp.get("message_id") or resp.get("id") or ""
                    sched_col = _find_col(headers, wb.get("scheduled", ""))
                    updates: List[Tuple[int, Any]] = []

                    if job_col is not None:
                        updates.append((job_col, job_id if job_id else str(resp)[:500]))
                    if sched_col is not None:
                        updates.append((sched_col, payload["scheduled_send_time"]))

                    for col_idx, value in updates:
                        a1 = cell_a1(row_num, col_idx)
                        sheets.write_range(
                            sheet_id=settings.SCRIPT_VIEW_SHEET_ID,
                            tab_name=settings.SCRIPT_VIEW_TAB_NAME,
                            a1_range=a1,
                            values=[[value]],
                            unredacted=False,
                        )

                except Exception as e:
                    errors.append(f"{platform}: {str(e)[:300]}")

            # Row-level writeback
            now_str = _now_utc().isoformat().replace("+00:00", "Z")
            final_status = "DONE" if (successes > 0 and not errors) else ("ERROR" if errors else "DONE")

            if col_status is not None:
                sheets.write_range(
                    sheet_id=settings.SCRIPT_VIEW_SHEET_ID,
                    tab_name=settings.SCRIPT_VIEW_TAB_NAME,
                    a1_range=cell_a1(row_num, col_status),
                    values=[[final_status]],
                    unredacted=False,
                )
            if col_last_run is not None:
                sheets.write_range(
                    sheet_id=settings.SCRIPT_VIEW_SHEET_ID,
                    tab_name=settings.SCRIPT_VIEW_TAB_NAME,
                    a1_range=cell_a1(row_num, col_last_run),
                    values=[[now_str]],
                    unredacted=False,
                )
            if col_last_err is not None:
                sheets.write_range(
                    sheet_id=settings.SCRIPT_VIEW_SHEET_ID,
                    tab_name=settings.SCRIPT_VIEW_TAB_NAME,
                    a1_range=cell_a1(row_num, col_last_err),
                    values=[["; ".join(errors)[:2000] if errors else ""]],
                    unredacted=False,
                )

            # Pacing delay between rows
            sleep_s = runtime.delay_base_seconds
            if runtime.delay_jitter_seconds_max >= runtime.delay_jitter_seconds_min:
                import random
                sleep_s += random.randint(runtime.delay_jitter_seconds_min, runtime.delay_jitter_seconds_max)
            time.sleep(sleep_s)

    finally:
        _release_lock(sheets, settings, lock_token)


if __name__ == "__main__":
    main()
