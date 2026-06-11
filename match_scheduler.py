#!/usr/bin/env python3
import os
import sys
import json
import time
import subprocess
from datetime import datetime, timezone, timedelta
import requests
import re

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
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    # Try as a Post first
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/{post_id}"
    payload = {
        "kind": "blogger#post",
        "id": post_id,
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        return response.json().get("url")
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            print("[*] Post ID not found, trying as a Blogger Page...")
        else:
            raise e
            
    # Try as a Page
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages/{post_id}"
    payload = {
        "kind": "blogger#page",
        "id": post_id,
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    response = requests.patch(url, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    return response.json().get("url")

def create_blogger_post(config, access_token, title, html_content):
    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "kind": "blogger#post",
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    response = requests.post(url, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    res_data = response.json()
    return res_data.get("id"), res_data.get("url")

def parse_time(time_str):
    if time_str.endswith("Z"):
        time_str = time_str[:-1] + "+00:00"
    return datetime.fromisoformat(time_str)

def write_direct_links(match_name, post_url, player_html):
    # Regex to extract the STREAM_LINKS list
    match_data = re.search(r'const STREAM_LINKS\s*=\s*(\[.*?\]);', player_html, re.DOTALL)
    if not match_data:
        return
    try:
        links_data = json.loads(match_data.group(1))
        
        # 1. Plain text format
        lines = []
        lines.append(f"==================================================")
        lines.append(f" DIRECT STREAM LINKS FOR: {match_name.upper()}")
        lines.append(f"==================================================\n")
        
        # 2. HTML format (clean list of paragraph links similar to RD9 Sports, styled for dark backgrounds)
        html_lines = []
        html_lines.append('<div style="font-family:\'Segoe UI\',Roboto,Helvetica,sans-serif; max-width:650px; margin: 15px auto;">')
        
        for idx, lnk in enumerate(links_data[:7]):
            label = lnk.get("label", f"Link {idx + 1}")
            meta = lnk.get("meta", "")
            direct_url = f"{post_url}?link={idx + 1}"
            
            # Plain text
            lines.append(f"{idx + 1}. {label} ({meta})")
            lines.append(f"   Link: {direct_url}\n")
            
            # HTML anchor formatted like RD9 Sports links
            display_meta = f" | {meta}" if meta else ""
            html_lines.append(f'  <p style="margin: 0 0 14px 0;">')
            html_lines.append(f'    <a href="{direct_url}" target="_blank" style="color:#00a8ff; text-decoration:none; font-size:15px; font-weight:bold; transition:color 0.2s;">{label}{display_meta}</a>')
            html_lines.append(f'  </p>')
            
        html_lines.append("</div>")
        
        html_content = "\n".join(html_lines)
        
        # Append the HTML format into the plain text format so they have it there too
        lines.append(f"==================================================")
        lines.append(f" PASTABLE HTML CODE (DARK THEME OPTIMIZED)")
        lines.append(f"==================================================\n")
        lines.append(html_content)
        
        content = "\n".join(lines)
        
        # Write plain text formats
        os.makedirs("links", exist_ok=True)
        with open(os.path.join("links", "direct_links_list.txt"), "w", encoding="utf-8") as f:
            f.write(content)
        safe_name = match_name.replace(' ', '_').lower()
        with open(os.path.join("links", f"links_{safe_name}.txt"), "w", encoding="utf-8") as f:
            f.write(content)
            
        # Write HTML formats
        with open(os.path.join("links", "direct_links_list.html"), "w", encoding="utf-8") as f:
            f.write(html_content)
        with open(os.path.join("links", f"links_{safe_name}.html"), "w", encoding="utf-8") as f:
            f.write(html_content)
            
        print(f"[+] Direct links list successfully written to plain text and HTML list files for {match_name}")
        print(content)
    except Exception as e:
        print(f"[-] Error writing direct links list: {e}")

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
            os.makedirs("players", exist_ok=True)
            temp_output = os.path.join("players", f"player_{match['match_name'].replace(' ', '_').lower()}.html")
            
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
            if has_oauth:
                try:
                    print(f"[*] Fetching access token...")
                    token = get_access_token(config)
                    post_title = match["match_name"] + " Live Stream"
                    
                    post_id = match.get("blogger_post_id")
                    if post_id and not post_id.startswith("YOUR_") and post_id.strip():
                        print(f"[*] Updating existing Blogger post {post_id}...")
                        post_url = update_blogger_post(config, token, post_id, post_title, player_html)
                    else:
                        print(f"[*] Creating a NEW Blogger post...")
                        post_id, post_url = create_blogger_post(config, token, post_title, player_html)
                        match["blogger_post_id"] = post_id
                        print(f"[+] Created new Blogger post with ID: {post_id}")
                        
                    print(f"[+] Blogger page updated successfully! URL: {post_url}")
                    match["blogger_post_url"] = post_url
                    match["iframe_embed_code"] = f'<iframe src="{post_url}" width="100%" height="480px" frameborder="0" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen style="background:#000;"></iframe>'
                    match["status"] = "completed"
                    # Generate the direct links list
                    write_direct_links(match["match_name"], post_url, player_html)
                except Exception as e:
                    print(f"[-] Blogger upload failed: {e}")
                    match["status"] = "failed"
            else:
                print("[!] Blogger OAuth not fully configured. Storing player HTML locally only.")
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
