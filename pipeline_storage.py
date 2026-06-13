#!/usr/bin/env python3
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

LEGACY_SCHEDULE_FILE = "match_schedule.json"
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
    end_hours = int(scheduler_config.get("active_window_end_hours", 3))
    return match_time - timedelta(minutes=start_offset), match_time + timedelta(hours=end_hours), match_time


def load_schedule(config=None, migrate_from_legacy=True):
    paths = storage_config(config)
    schedule = read_json(paths["schedule_file"], None)
    if isinstance(schedule, list):
        return schedule
    if migrate_from_legacy and paths["schedule_file"] != LEGACY_SCHEDULE_FILE:
        legacy_schedule = read_json(LEGACY_SCHEDULE_FILE, None)
        if isinstance(legacy_schedule, list):
            return legacy_schedule
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


def archive_completed_matches(schedule, config=None, scheduler_config=None, now=None):
    paths = ensure_runtime_dirs(config)
    now = now or datetime.now(timezone.utc)
    scheduler_config = scheduler_config or (config or {}).get("scheduler", {})
    active = []
    archived = []

    for match in schedule:
        should_archive = match.get("status") == "completed"
        if not should_archive:
            try:
                _, run_end, _ = active_window(match, scheduler_config)
                should_archive = now > run_end and match.get("status") in ("ended", "done")
            except Exception:
                should_archive = False
        if should_archive:
            archived.append(match)
        else:
            active.append(match)

    if not archived:
        return active, []

    grouped = {}
    for match in archived:
        try:
            bucket = parse_time(match.get("match_time")).astimezone(IST).strftime("%Y-%m")
        except Exception:
            bucket = now.astimezone(IST).strftime("%Y-%m")
        entry = dict(match)
        entry["archived_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        entry["match_key"] = entry.get("match_key") or match_key(match)
        grouped.setdefault(bucket, []).append(entry)

    for bucket, entries in grouped.items():
        history_path = os.path.join(paths["history_dir"], f"{bucket}.jsonl")
        Path(history_path).parent.mkdir(parents=True, exist_ok=True)
        with open(history_path, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n")

    return active, archived
