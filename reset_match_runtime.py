#!/usr/bin/env python3
import argparse
from datetime import datetime, timedelta, timezone

from automation_config import load_automation_config
from pipeline_storage import IST, load_schedule, match_key, parse_time, save_schedule

RUNTIME_FIELDS = (
    "status",
    "last_run_time",
    "new_blog_iframe_set",
    "new_blog_prepare_set",
    "new_blog_ended_set",
    "iframe_embed_code",
)


def resolve_selector(value, now):
    selector = str(value or "").strip().lower()
    today = now.astimezone(IST).date()
    if selector == "today":
        return today
    if selector == "tomorrow":
        return today + timedelta(days=1)
    return datetime.strptime(selector, "%Y-%m-%d").date()


def in_scope(match, target_date, within_hours, now):
    try:
        kickoff = parse_time(match.get("match_time"))
    except Exception:
        return False
    if target_date and kickoff.astimezone(IST).date() != target_date:
        return False
    if within_hours is not None and not (now <= kickoff <= now + timedelta(hours=within_hours)):
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Reset only runtime scheduler flags for selected matches.")
    parser.add_argument("--for-ist-date", help="today, tomorrow, or YYYY-MM-DD")
    parser.add_argument("--within-hours", type=float, help="Reset matches kicking off within the next N hours.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without saving.")
    args = parser.parse_args()

    if args.for_ist_date and args.within_hours is not None:
        parser.error("Choose either --for-ist-date or --within-hours, not both.")

    now = datetime.now(timezone.utc)
    target_date = resolve_selector(args.for_ist_date, now) if args.for_ist_date else None
    config = load_automation_config()
    schedule = load_schedule(config)
    changed = False

    for match in schedule:
        if not in_scope(match, target_date, args.within_hours, now):
            continue
        removed = []
        for field in RUNTIME_FIELDS:
            if field in match:
                match.pop(field, None)
                removed.append(field)
        match["status"] = "pending"
        match["match_key"] = match.get("match_key") or match_key(match)
        changed = bool(removed) or changed
        print(f"[*] {match.get('match_name')}: reset {', '.join(removed) if removed else 'status only'}")

    if changed and not args.dry_run:
        save_schedule(schedule, config)
        print("[+] Runtime flags reset and schedule saved.")
    elif changed:
        print("[+] Dry run complete. Schedule was not saved.")
    else:
        print("[+] No matching runtime flags needed reset.")


if __name__ == "__main__":
    main()
