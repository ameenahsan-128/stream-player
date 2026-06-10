#!/usr/bin/env python3
import os
import sys
import json
import time
import subprocess
from datetime import datetime, timezone, timedelta
import requests

SCHEDULE_FILE = "match_schedule.json"
CONFIG_FILE = "blogger_config.json"

def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def get_access_token(config):
    url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": config.get("client_id"),
        "client_secret": config.get("client_secret"),
        "refresh_token": config.get("refresh_token"),
        "grant_type": "refresh_token"
    }
    response = requests.post(url, data=payload, timeout=15)
    response.raise_for_status()
    return response.json().get("access_token")

def update_blogger_post(config, access_token, post_id, title, html_content):
    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/{post_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "kind": "blogger#post",
        "id": post_id,
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    response = requests.patch(url, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    return response.json().get("url")

def parse_time(time_str):
    if time_str.endswith("Z"):
        time_str = time_str[:-1] + "+00:00"
    return datetime.fromisoformat(time_str)

def check_and_run():
    schedule = load_json(SCHEDULE_FILE)
    config = load_json(CONFIG_FILE)
    if not schedule or not config:
        return

    # Check if we have valid OAuth details
    has_oauth = all(config.get(k) and not config[k].startswith("YOUR_") 
                    for k in ["client_id", "client_secret", "refresh_token", "blog_id"])

    changed = False
    now = datetime.now(timezone.utc)

    for match in schedule:
        if match.get("status") != "pending":
            continue

        match_time = parse_time(match["match_time"])
        # Run if we are within 15 minutes before the match start, up to 2 hours after it starts
        run_start = match_time - timedelta(minutes=15)
        run_end = match_time + timedelta(hours=2)

        if run_start <= now <= run_end:
            print(f"[*] Starting process for match: {match['match_name']}")
            match["status"] = "processing"
            save_json(SCHEDULE_FILE, schedule) # Save status immediately

            # Define output file name
            temp_output = f"player_{match['match_name'].replace(' ', '_').lower()}.html"
            
            # Step 1: Run generate_player.py to crawl and produce player file
            print(f"[*] Scraping {match['source_url']}...")
            try:
                cmd = ["python3", "generate_player.py", "-u", match["source_url"], "-o", temp_output]
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                print(f"[+] Scraping successful. Generated {temp_output}")
            except Exception as e:
                print(f"[-] Scraping failed: {e}")
                match["status"] = "failed"
                changed = True
                continue

            # Read player HTML content
            if os.path.exists(temp_output):
                with open(temp_output, "r", encoding="utf-8") as pf:
                    player_html = pf.read()
            else:
                print(f"[-] Output file {temp_output} not found.")
                match["status"] = "failed"
                changed = True
                continue

            # Step 2: Push to Blogger if OAuth is set up
            if has_oauth and match.get("blogger_post_id") and not match["blogger_post_id"].startswith("YOUR_"):
                try:
                    print(f"[*] Fetching access token and updating Blogger post {match['blogger_post_id']}...")
                    token = get_access_token(config)
                    post_url = update_blogger_post(config, token, match["blogger_post_id"], match["match_name"] + " Live Stream", player_html)
                    print(f"[+] Blogger page updated successfully! URL: {post_url}")
                    match["blogger_post_url"] = post_url
                    match["status"] = "completed"
                except Exception as e:
                    print(f"[-] Blogger upload failed: {e}")
                    match["status"] = "failed"
            else:
                print("[!] Blogger OAuth or Post ID not fully configured. Storing player HTML locally only.")
                match["status"] = "completed_local"
            
            changed = True

    if changed:
        save_json(SCHEDULE_FILE, schedule)

def main():
    print("[*] Match Scheduler started. Checking every 60 seconds...")
    while True:
        try:
            check_and_run()
        except Exception as e:
            print(f"[-] Scheduler iteration failed: {e}")
        time.sleep(60)

if __name__ == "__main__":
    main()
