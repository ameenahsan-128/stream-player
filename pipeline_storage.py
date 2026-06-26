#!/usr/bin/env python3
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_DATA_DIR = "data"
DEFAULT_SCHEDULE_FILE = os.path.join(DEFAULT_DATA_DIR, "match_schedule.json")
DEFAULT_HISTORY_DIR = os.path.join(DEFAULT_DATA_DIR, "history")
DEFAULT_RUNTIME_STATE_FILE = os.path.join(DEFAULT_DATA_DIR, "runtime_state.json")
DEFAULT_PLAYERS_DIR = os.path.join(DEFAULT_DATA_DIR, "players")
DEFAULT_LINKS_DIR = os.path.join(DEFAULT_DATA_DIR, "links")
DEFAULT_DIAGNOSTICS_DIR = os.path.join(DEFAULT_DATA_DIR, "scraped_details")
DEFAULT_THUMBNAILS_DIR = os.path.join(DEFAULT_DATA_DIR, "thumbnails")
DEFAULT_LOCK_FILE = os.path.join(DEFAULT_DATA_DIR, "scheduler.lock")


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def storage_config(config=None):
    scheduler = (config or {}).get("scheduler", {})
    return {
        "data_dir": scheduler.get("data_dir") or DEFAULT_DATA_DIR,
        "schedule_file": scheduler.get("schedule_file") or DEFAULT_SCHEDULE_FILE,
        "history_dir": scheduler.get("history_dir") or DEFAULT_HISTORY_DIR,
        "runtime_state_file": scheduler.get("runtime_state_file") or DEFAULT_RUNTIME_STATE_FILE,
        "players_dir": scheduler.get("players_dir") or DEFAULT_PLAYERS_DIR,
        "links_dir": scheduler.get("links_dir") or DEFAULT_LINKS_DIR,
        "diagnostics_dir": scheduler.get("diagnostics_dir") or DEFAULT_DIAGNOSTICS_DIR,
        "thumbnails_dir": scheduler.get("thumbnails_dir") or DEFAULT_THUMBNAILS_DIR,
        "lock_file": scheduler.get("lock_file") or DEFAULT_LOCK_FILE,
    }


def ensure_runtime_dirs(config=None):
    paths = storage_config(config)
    for key in ("data_dir", "history_dir", "players_dir", "links_dir", "diagnostics_dir", "thumbnails_dir"):
        Path(paths[key]).mkdir(parents=True, exist_ok=True)
    return paths


def parse_time(value):
    value = str(value or "").strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def active_window(match, scheduler_config):
    match_time = parse_time(match["match_time"])
    start_offset = int(scheduler_config.get("active_window_start_minutes", 15))
    end_hours = float(scheduler_config.get("active_window_end_hours", 3))
    return match_time - timedelta(minutes=start_offset), match_time + timedelta(hours=end_hours), match_time


def load_schedule(config=None, migrate_from_legacy=True):
    paths = storage_config(config)
    schedule = read_json(paths["schedule_file"], None)
    if isinstance(schedule, list):
        return schedule
    return []


def save_schedule(schedule, config=None):
    paths = ensure_runtime_dirs(config)
    write_json(paths["schedule_file"], schedule)


def match_key(match):
    name = str(match.get("match_name") or "match").strip().lower()
    slug = "_".join(part for part in "".join(ch if ch.isalnum() else " " for ch in name).split() if part)
    try:
        kickoff = parse_time(match.get("match_time")).astimezone(IST).strftime("%Y%m%d_%H%M_ist")
    except Exception:
        kickoff = "tba"
    return f"{kickoff}_{slug or 'match'}"


def strip_runtime_fields(match):
    cleaned = dict(match)
    for key in (
        "status",
        "last_run_time",
        "new_blog_iframe_set",
        "new_blog_prepare_set",
        "iframe_embed_code",
    ):
        cleaned.pop(key, None)
    return cleaned


def archive_identity(match):
    return str(match.get("fixture_id") or match.get("match_key") or match_key(match))


def archive_entries(entries, config=None, now=None, reason="completed"):
    paths = ensure_runtime_dirs(config)
    now = now or datetime.now(timezone.utc)
    grouped = {}
    for match in entries or []:
        try:
            bucket = parse_time(match.get("match_time")).astimezone(IST).strftime("%Y-%m")
        except Exception:
            bucket = now.astimezone(IST).strftime("%Y-%m")
        entry = dict(match)
        entry["archived_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        entry["archive_reason"] = reason
        entry["match_key"] = entry.get("match_key") or match_key(match)
        entry["_archive_identity"] = archive_identity(entry)
        grouped.setdefault(bucket, []).append(entry)

    written = []
    for bucket, bucket_entries in grouped.items():
        history_path = os.path.join(paths["history_dir"], f"{bucket}.jsonl")
        Path(history_path).parent.mkdir(parents=True, exist_ok=True)
        existing = set()
        if os.path.exists(history_path):
            with open(history_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        existing_entry = json.loads(line)
                    except Exception:
                        continue
                    existing.add(
                        str(existing_entry.get("_archive_identity") or archive_identity(existing_entry))
                    )
        with open(history_path, "a", encoding="utf-8") as f:
            for entry in bucket_entries:
                identity = str(entry.get("_archive_identity") or archive_identity(entry))
                if identity in existing:
                    continue
                f.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")
                existing.add(identity)
                written.append(entry)
    return written


def _match_is_completed(match, now=None):
    """Return True if the match should be considered completed for archival."""
    if match.get("status") == "completed":
        return True

    # A final result for a future match is almost certainly bogus metadata
    # (e.g. a Wikipedia group table read as a score). Only trust it when the
    # match time has already passed.
    now = now or datetime.now(timezone.utc)
    try:
        match_time = parse_time(match.get("match_time"))
        result_in_past = match_time <= now
    except Exception:
        result_in_past = True

    result = match.get("result") if isinstance(match.get("result"), dict) else {}
    if result.get("status") == "final" and result_in_past:
        return True
    if match.get("match_status") == "completed" and result_in_past:
        return True
    return False


def archive_completed_matches(schedule, config=None, scheduler_config=None, now=None):
    ensure_runtime_dirs(config)
    now = now or datetime.now(timezone.utc)
    scheduler_config = scheduler_config or (config or {}).get("scheduler", {})
    active = []
    archived = []

    for match in schedule:
        should_archive = _match_is_completed(match, now=now)
        if should_archive:
            # Normalize status so downstream logic does not re-process it
            if match.get("status") != "completed":
                match["status"] = "completed"
            archived.append(match)
        else:
            active.append(match)

    if not archived:
        return active, []

    archive_entries(archived, config=config, now=now, reason="completed")

    return active, archived
