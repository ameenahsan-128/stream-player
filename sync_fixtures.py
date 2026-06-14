#!/usr/bin/env python3
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import requests

from automation_config import get_fixture_api_config, get_scheduler_config, load_automation_config
from fixture_manager import merge_schedule_with_fixtures, normalize_fixture_list
from pipeline_storage import archive_entries, load_schedule, parse_time, save_schedule


def read_json_list(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        for key in ("fixtures", "matches", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    if isinstance(data, list):
        return data
    raise ValueError(f"fixture file must contain a list or fixtures/matches/data list: {path}")


def load_fixture_file(path):
    if not path or not os.path.exists(path):
        raise FileNotFoundError(path or "<empty fixture path>")
    print(f"[*] Loading canonical fixtures from file: {path}")
    fixtures = read_json_list(path)
    print(f"[+] Loaded {len(fixtures)} fixture row(s) from file.")
    return fixtures, f"file:{path}"


def load_fixture_api(url):
    if not url:
        raise ValueError("fixture API URL is empty")
    print(f"[*] Fetching fixtures from API: {url}")
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        for key in ("fixtures", "matches", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError("fixture API response must be a list or contain fixtures/matches/data list")
    print(f"[+] Loaded {len(data)} fixture row(s) from API.")
    return data, f"api:{url}"


def load_fixture_source(api_config, fixture_file_override=None):
    provider = str(api_config.get("provider") or "local_file").strip().lower()
    fixture_file = fixture_file_override or api_config.get("fixture_file")

    if provider in ("local", "local_file", "file", "fixture_file"):
        try:
            return load_fixture_file(fixture_file)
        except Exception as exc:
            print(f"[!] Canonical fixture file unavailable: {exc}")
    elif provider in ("http", "api"):
        if api_config.get("allow_http_api"):
            try:
                return load_fixture_api(api_config.get("url"))
            except Exception as exc:
                print(f"[!] Fixture API fetch failed: {exc}")
        else:
            print("[!] HTTP fixture API is configured but allow_http_api is false; skipping API fetch.")
    else:
        print(f"[!] Unknown fixture provider '{provider}'.")

    if api_config.get("allow_mock_fallback") and api_config.get("mock_fallback"):
        try:
            print("[*] Using explicitly allowed mock fixture fallback.")
            return load_fixture_file(api_config.get("mock_fallback"))
        except Exception as exc:
            print(f"[!] Mock fallback failed: {exc}")

    return [], provider


def sync_fixtures(dry_run=False, fixture_file=None, prune_unsynced=None):
    config = load_automation_config()
    api_config = get_fixture_api_config(config)
    scheduler_config = get_scheduler_config(config)

    if not api_config.get("enabled", True):
        print("[*] Fixture synchronization is disabled in configuration.")
        return {"changed": False, "stats": {}}

    raw_fixtures, source_name = load_fixture_source(api_config, fixture_file_override=fixture_file)
    if not raw_fixtures:
        print("[!] No canonical fixtures loaded; active schedule was not changed.")
        return {"changed": False, "stats": {}}

    fixtures, errors = normalize_fixture_list(raw_fixtures, source_name=source_name)
    for idx, error in errors:
        print(f"[!] Skipped invalid fixture row {idx}: {error}")
    if not fixtures:
        print("[!] No valid fixtures after normalization; active schedule was not changed.")
        return {"changed": False, "stats": {}}
    now = datetime.now(timezone.utc)
    active_end_hours = float(scheduler_config.get("active_window_end_hours") or 3)
    completion_grace_hours = float(scheduler_config.get("completion_score_grace_hours") or 12)
    active_fixtures = []
    skipped_past = 0
    for fixture in fixtures:
        try:
            retain_until = parse_time(fixture.get("match_time")) + timedelta(
                hours=active_end_hours + completion_grace_hours
            )
            if retain_until < now:
                skipped_past += 1
                continue
        except Exception:
            pass
        active_fixtures.append(fixture)
    fixtures = active_fixtures
    if skipped_past:
        print(f"[*] Skipped {skipped_past} fixture(s) beyond the completion score grace window.")
    if not fixtures:
        print("[!] No active/upcoming fixtures remain after past-fixture filtering.")
        return {"changed": False, "stats": {}}

    prune = api_config.get("prune_unsynced", True) if prune_unsynced is None else prune_unsynced
    tolerance_hours = float(api_config.get("merge_tolerance_hours") or 18)
    schedule = load_schedule(config)
    next_schedule, removed, stats = merge_schedule_with_fixtures(
        schedule,
        fixtures,
        prune_unsynced=bool(prune),
        tolerance_hours=tolerance_hours,
    )
    changed = next_schedule != schedule or bool(removed)

    print(
        "[+] Fixture merge summary: "
        f"added={stats.get('added', 0)}, updated={stats.get('updated', 0)}, "
        f"deduped={stats.get('deduped', 0)}, removed={stats.get('removed', 0)}"
    )

    if dry_run:
        for match in removed:
            print(f"[dry-run] Would remove out-of-scope match: {match.get('match_name')}")
        print("[+] Dry run complete. Schedule was not saved.")
        return {"changed": changed, "stats": stats, "removed": removed}

    if changed:
        if removed:
            archived = archive_entries(removed, config=config, now=now, reason="out_of_scope")
            print(f"[*] Archived {len(archived)} out-of-scope match(es).")
        save_schedule(next_schedule, config)
        print("[+] Fixture schedule synchronized and saved.")
    else:
        print("[+] Fixture schedule is already up to date.")
    return {"changed": changed, "stats": stats, "removed": removed}


def main():
    parser = argparse.ArgumentParser(description="Synchronize active schedule from canonical World Cup fixtures.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without saving.")
    parser.add_argument("--fixture-file", help="Override configured canonical fixture JSON file.")
    parser.add_argument("--no-prune", action="store_true", help="Keep schedule rows that are not in the fixture feed.")
    args = parser.parse_args()
    sync_fixtures(
        dry_run=args.dry_run,
        fixture_file=args.fixture_file,
        prune_unsynced=False if args.no_prune else None,
    )


if __name__ == "__main__":
    main()
