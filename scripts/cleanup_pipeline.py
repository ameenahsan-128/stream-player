#!/usr/bin/env python3
"""
Pipeline Cleanup Script
=======================
Removes all generated/stale files and prunes the match schedule to keep only
upcoming and ongoing matches. Run this to start fresh.

Usage:
    python3 scripts/cleanup_pipeline.py              # Dry-run (shows what would be deleted)
    python3 scripts/cleanup_pipeline.py --execute     # Actually delete files
    python3 scripts/cleanup_pipeline.py --execute --keep-thumbnails  # Keep thumbnails
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone, timedelta

# Project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")


def human_size(size_bytes):
    """Convert bytes to human-readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}TB"


def get_dir_size(path):
    """Get total size of a directory."""
    total = 0
    if os.path.isfile(path):
        return os.path.getsize(path)
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total += os.path.getsize(fp)
    return total


def cleanup_directory(path, label, execute=False):
    """Remove all files in a directory (but keep the directory itself)."""
    if not os.path.exists(path):
        print(f"  ⏭ {label}: directory does not exist, skipping")
        return 0

    files = []
    for root, dirs, fnames in os.walk(path):
        for fname in fnames:
            files.append(os.path.join(root, fname))

    if not files:
        print(f"  ✅ {label}: already clean (0 files)")
        return 0

    total_size = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    print(f"  {'🗑' if execute else '📋'} {label}: {len(files)} file(s), {human_size(total_size)}")

    if execute:
        for f in files:
            try:
                os.remove(f)
            except Exception as e:
                print(f"    ⚠ Failed to remove {f}: {e}")
        # Remove empty subdirectories
        for root, dirs, fnames in os.walk(path, topdown=False):
            for d in dirs:
                dp = os.path.join(root, d)
                try:
                    if not os.listdir(dp):
                        os.rmdir(dp)
                except Exception:
                    pass

    return len(files)


def cleanup_file(path, label, execute=False):
    """Remove a single file."""
    if not os.path.exists(path):
        print(f"  ⏭ {label}: does not exist, skipping")
        return 0

    size = os.path.getsize(path)
    print(f"  {'🗑' if execute else '📋'} {label}: {human_size(size)}")

    if execute:
        try:
            os.remove(path)
        except Exception as e:
            print(f"    ⚠ Failed to remove {path}: {e}")
    return 1


def prune_match_schedule(execute=False, grace_hours=3):
    """Remove ended matches from the schedule. Keep pending/ongoing + recently ended within grace_hours."""
    schedule_path = os.path.join(DATA_DIR, "match_schedule.json")
    if not os.path.exists(schedule_path):
        print(f"  ⏭ Match schedule: does not exist")
        return

    try:
        with open(schedule_path, "r", encoding="utf-8") as f:
            schedule = json.load(f)
    except Exception as e:
        print(f"  ⚠ Failed to load match schedule: {e}")
        return

    now = datetime.now(timezone.utc)
    kept = []
    removed = []

    for match in schedule:
        status = match.get("status", "pending")
        match_name = match.get("match_name", "Unknown")
        match_time_str = match.get("match_time", "")

        try:
            match_time = datetime.fromisoformat(match_time_str.replace("Z", "+00:00"))
        except Exception:
            match_time = None

        # Keep if:
        # 1. Status is pending (upcoming)
        # 2. Status is active/live
        # 3. Match time is in the future
        # 4. Match ended recently (within grace_hours) — for post-match cleanup by scheduler
        should_keep = False

        if status in ("pending", "active", "live", "pre_kickoff"):
            should_keep = True
        elif match_time and match_time > now:
            should_keep = True
        elif status == "ended":
            # Check if it ended recently (match_time + 2h match duration + grace)
            if match_time:
                match_end_approx = match_time + timedelta(hours=2 + grace_hours)
                if match_end_approx > now:
                    should_keep = True

        if should_keep:
            kept.append(match)
        else:
            removed.append(match_name)

    print(f"  📊 Match schedule: {len(schedule)} total → keeping {len(kept)}, removing {len(removed)}")
    for name in removed:
        print(f"    ❌ Removing: {name}")
    for m in kept:
        mt = m.get("match_time", "")[:16]
        st = m.get("status", "?")
        print(f"    ✅ Keeping:  {m.get('match_name', '?'):30s} | {st:8s} | {mt}")

    if execute and removed:
        with open(schedule_path, "w", encoding="utf-8") as f:
            json.dump(kept, f, indent=2)
        print(f"  ✅ Schedule updated: {len(kept)} matches saved")


def reset_domain_health(execute=False):
    """Reset domain health to clean state."""
    health_path = os.path.join(DATA_DIR, "domain_health.json")
    if not os.path.exists(health_path):
        print(f"  ⏭ Domain health: does not exist")
        return

    if execute:
        with open(health_path, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2)
        print(f"  🗑 Domain health: reset to empty")
    else:
        print(f"  📋 Domain health: would be reset to empty")


def reset_runtime_state(execute=False):
    """Reset runtime state."""
    state_path = os.path.join(DATA_DIR, "runtime_state.json")
    if not os.path.exists(state_path):
        print(f"  ⏭ Runtime state: does not exist")
        return

    if execute:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2)
        print(f"  🗑 Runtime state: reset to empty")
    else:
        print(f"  📋 Runtime state: would be reset to empty")


def main():
    parser = argparse.ArgumentParser(description="Cleanup pipeline generated files and prune match schedule.")
    parser.add_argument("--execute", action="store_true", help="Actually delete files (without this flag, it's a dry run)")
    parser.add_argument("--keep-thumbnails", action="store_true", help="Keep thumbnail files")
    parser.add_argument("--keep-history", action="store_true", help="Keep history files")
    parser.add_argument("--grace-hours", type=float, default=3, help="Hours after match end to keep match in schedule (default: 3)")
    args = parser.parse_args()

    mode = "🔴 EXECUTE MODE" if args.execute else "🔵 DRY RUN (add --execute to actually delete)"
    print(f"\n{'=' * 60}")
    print(f"  Pipeline Cleanup — {mode}")
    print(f"{'=' * 60}\n")

    total_removed = 0

    # 1. Generated player HTML files
    print("📁 Generated Players (data/players/)")
    total_removed += cleanup_directory(os.path.join(DATA_DIR, "players"), "Players", args.execute)

    # 2. Generated embeds
    print("\n📁 Generated Embeds (data/players/embeds/ + embeds/)")
    total_removed += cleanup_directory(os.path.join(DATA_DIR, "players", "embeds"), "Player embeds", args.execute)
    total_removed += cleanup_directory(os.path.join(PROJECT_ROOT, "embeds"), "Root embeds", args.execute)

    # 3. Generated links HTML
    print("\n📁 Generated Links (data/links/)")
    total_removed += cleanup_directory(os.path.join(DATA_DIR, "links"), "Links", args.execute)

    # 4. Scraped details/diagnostics
    print("\n📁 Scraped Details (data/scraped_details/)")
    total_removed += cleanup_directory(os.path.join(DATA_DIR, "scraped_details"), "Scraped details", args.execute)

    # 5. Thumbnails (optional)
    if not args.keep_thumbnails:
        print("\n📁 Thumbnails (data/thumbnails/)")
        total_removed += cleanup_directory(os.path.join(DATA_DIR, "thumbnails"), "Thumbnails", args.execute)
    else:
        print("\n📁 Thumbnails: keeping (--keep-thumbnails)")

    # 6. History (optional)
    if not args.keep_history:
        print("\n📁 History (data/history/)")
        total_removed += cleanup_directory(os.path.join(DATA_DIR, "history"), "History", args.execute)
    else:
        print("\n📁 History: keeping (--keep-history)")

    # 7. Root-level generated files
    print("\n📄 Root-level generated files")
    for fname in ["test_player.html", "iframes.html", "player.html"]:
        total_removed += cleanup_file(os.path.join(PROJECT_ROOT, fname), fname, args.execute)

    # 8. Log files
    print("\n📄 Log files")
    total_removed += cleanup_file(os.path.join(DATA_DIR, "scheduler.log"), "scheduler.log", args.execute)
    total_removed += cleanup_file(os.path.join(DATA_DIR, "precreate.log"), "precreate.log", args.execute)

    # 9. Lock file
    print("\n📄 Lock file")
    total_removed += cleanup_file(os.path.join(DATA_DIR, "scheduler.lock"), "scheduler.lock", args.execute)

    # 10. Scratch experiment results
    print("\n📄 Scratch experiment results")
    total_removed += cleanup_file(
        os.path.join(PROJECT_ROOT, "scratch", "scraper_comparison_results.json"),
        "scraper_comparison_results.json", args.execute
    )

    # 11. Domain health & runtime state
    print("\n🔧 Pipeline State")
    reset_domain_health(args.execute)
    reset_runtime_state(args.execute)

    # 12. Match schedule pruning
    print("\n📅 Match Schedule Pruning")
    prune_match_schedule(args.execute, grace_hours=args.grace_hours)

    # Summary
    print(f"\n{'=' * 60}")
    if args.execute:
        print(f"  ✅ Cleanup complete: {total_removed} file(s) removed")
    else:
        print(f"  📋 Dry run complete: {total_removed} file(s) would be removed")
        print(f"  Run with --execute to actually delete files")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
