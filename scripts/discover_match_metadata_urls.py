#!/usr/bin/env python3
import argparse

from automation_config import get_scheduler_config, load_automation_config
from match_metadata import discover_metadata_urls_for_match, add_metadata_url
from pipeline_storage import load_schedule, save_schedule


def main():
    parser = argparse.ArgumentParser(description="Discover trusted metadata URLs for active matches.")
    parser.add_argument("--save", action="store_true", help="Save discovered metadata URLs into the active schedule.")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of matches to process.")
    args = parser.parse_args()

    config = load_automation_config()
    scheduler_config = get_scheduler_config(config)
    schedule = load_schedule(config)
    changed = False
    processed = 0

    for match in schedule:
        if match.get("status") == "completed":
            continue
        if args.limit and processed >= args.limit:
            break
        processed += 1
        urls = discover_metadata_urls_for_match(match, scheduler_config)
        if not urls:
            print(f"{match.get('match_name')}: no new metadata URLs")
            continue
        print(f"{match.get('match_name')}:")
        for url in urls:
            print(f"  - {url}")
            if args.save and add_metadata_url(match, url, role="search"):
                changed = True

    if args.save and changed:
        save_schedule(schedule, config)
        print("[+] Schedule updated with discovered metadata URLs.")
    elif args.save:
        print("[+] No schedule changes.")
    else:
        print("[*] Dry run only. Re-run with --save to update the schedule.")


if __name__ == "__main__":
    main()
