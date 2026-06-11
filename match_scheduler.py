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

def generate_external_link_list(match_name, post_url, player_html):
    import re
    import html as html_module
    
    links = []
    # Extract STREAM_LINKS array from player HTML using regex
    js_match = re.search(r'const STREAM_LINKS = (\[.*?\]);', player_html, re.DOTALL)
    if js_match:
        try:
            links = json.loads(js_match.group(1))
        except Exception as e:
            print(f"[-] Failed to parse STREAM_LINKS JSON: {e}")
            
    if not links:
        links = [{"label": "Stream Link 1", "type": "auto", "meta": "Auto Video Type"}]

    html_lines = []
    html_lines.append(f"""<!-- START MATCH LINK BLOCK FOR {match_name} -->
<div class="match-links-widget" style="background:#0c0d14; border:1px solid #1e2230; border-radius:12px; padding:20px; font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif; max-width:500px; margin:20px auto; box-shadow:0 8px 30px rgba(0,0,0,0.5);">
  <h2 style="margin:0 0 6px 0; font-size:18px; color:#ffffff; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; font-family:sans-serif;">{html_module.escape(match_name)}</h2>
  <p style="margin:0 0 16px 0; font-size:12px; color:#8a94a6;">Select a stream link below to watch directly on our Blogger player:</p>
  <div style="display:flex; flex-direction:column; gap:10px;">""")

    for idx, lnk in enumerate(links, 1):
        label = lnk.get("label", f"Link {idx}")
        blogger_stream_url = f"{post_url}?stream={idx}"
        meta = lnk.get("meta", "Live Stream")
        badges = lnk.get("badges", [])
        
        badge_html = ""
        for badge in badges:
            bg_color = "#3498db"
            if badge == "hls": bg_color = "#e67e22"
            elif badge == "hd": bg_color = "#2ecc71"
            elif badge == "sd": bg_color = "#95a5a6"
            elif badge == "iframe": bg_color = "#9b59b6"
            badge_html += f'<span style="background:{bg_color}; color:#fff; font-size:9px; font-weight:bold; padding:2px 6px; border-radius:3px; text-transform:uppercase; margin-left:6px;">{html_module.escape(badge.upper())}</span>'

        html_lines.append(f"""    <a href="{blogger_stream_url}" target="_blank" style="display:flex; align-items:center; justify-content:space-between; padding:12px 16px; background:#161925; border:1px solid #23283b; border-radius:8px; color:#ffffff; text-decoration:none; font-weight:500; font-size:14px; transition:all 0.2s ease-in-out;" onmouseover="this.style.background='#1f2434'; this.style.borderColor='#0088cc'" onmouseout="this.style.background='#161925'; this.style.borderColor='#23283b'">
      <span style="display:flex; align-items:center;">
        <span style="color:#0088cc; font-weight:bold; margin-right:8px;">▶</span> {html_module.escape(label)}
      </span>
      <span style="display:flex; align-items:center; font-size:12px; color:#5c677d;">
        {meta} {badge_html}
      </span>
    </a>""")

    html_lines.append("""  </div>
</div>
<!-- END MATCH LINK BLOCK -->""")

    return "\n".join(html_lines)

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

            # Support both list and string for source_url
            source_urls = match.get("source_url", [])
            if isinstance(source_urls, str):
                source_urls = [source_urls]

            # Define output file name
            temp_output = f"player_{match['match_name'].replace(' ', '_').lower()}.html"
            
            # Step 1: Run generate_player.py to crawl and produce player file
            print(f"[*] Scraping {len(source_urls)} source(s): {', '.join(source_urls)}...")
            try:
                cmd = ["python3", "generate_player.py", "-u"] + source_urls + ["-o", temp_output]
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
                    
                    # Generate the external links list HTML file
                    try:
                        links_filename = f"links_{match['match_name'].replace(' ', '_').lower()}.html"
                        links_html = generate_external_link_list(match["match_name"], post_url, player_html)
                        with open(links_filename, "w", encoding="utf-8") as lf:
                            lf.write(links_html)
                        print(f"[+] Generated external Blogger links list: {links_filename}")
                        match["external_links_file"] = links_filename
                    except Exception as le:
                        print(f"[-] Failed to generate external links list: {le}")
                    
                    match["status"] = "completed"
                except Exception as e:
                    print(f"[-] Blogger upload failed: {e}")
                    match["status"] = "failed"
            else:
                print("[!] Blogger OAuth not fully configured. Storing player HTML locally only.")
                # Generate external links list referencing local player file
                try:
                    local_url = f"player_{match['match_name'].replace(' ', '_').lower()}.html"
                    links_filename = f"links_{match['match_name'].replace(' ', '_').lower()}.html"
                    links_html = generate_external_link_list(match["match_name"], local_url, player_html)
                    with open(links_filename, "w", encoding="utf-8") as lf:
                        lf.write(links_html)
                    print(f"[+] Generated local external links list: {links_filename}")
                    match["external_links_file"] = links_filename
                except Exception as le:
                    print(f"[-] Failed to generate local external links list: {le}")
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
