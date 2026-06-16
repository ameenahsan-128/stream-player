from datetime import datetime, timezone, timedelta
import json
import os
from match_scheduler import (
    load_automation_config,
    get_scheduler_config,
    active_window,
    scrape_cooldown_minutes,
    parse_time,
    match_key
)

def check():
    with open("data/match_schedule.json", "r", encoding="utf-8") as f:
        schedule = json.load(f)
    
    automation_config = load_automation_config()
    scheduler_config = get_scheduler_config(automation_config)
    now = datetime.now(timezone.utc)
    
    print(f"Current UTC time: {now.isoformat()}")
    
    for match in schedule:
        if "Spain" in match["match_name"]:
            status = match.get("status", "pending")
            print(f"Match Name: {match['match_name']}")
            print(f"Status: {status}")
            match_time = parse_time(match["match_time"])
            run_start, run_end, duration_hours = active_window(match, scheduler_config)
            print(f"Match Time: {match_time.isoformat()}")
            print(f"Run Start: {run_start.isoformat()}")
            print(f"Run End: {run_end.isoformat()}")
            print(f"Active Window Active?: {run_start <= now <= run_end}")
            
            cooldown_min = scrape_cooldown_minutes(now, match_time, scheduler_config)
            print(f"Cooldown Minutes: {cooldown_min}")
            
            last_run_str = match.get("last_run_time")
            print(f"Last Run Time: {last_run_str}")
            
            if last_run_str:
                last_run_dt = parse_time(last_run_str)
                diff = now - last_run_dt
                print(f"Time since last run: {diff.total_seconds() / 60:.2f} minutes")
                print(f"Is diff >= cooldown?: {diff >= timedelta(minutes=cooldown_min)}")
            else:
                print("No last run time set.")

if __name__ == "__main__":
    check()
