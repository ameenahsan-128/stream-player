#!/usr/bin/env python3
import os
import sys
import json
import time
import subprocess
from datetime import datetime, timezone, timedelta
import requests
import re
from urllib.parse import urlparse, urljoin

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
        html_lines.append('  <style>')
        html_lines.append('    .stream-btn {')
        html_lines.append('      display: inline-block;')
        html_lines.append('      width: 100%;')
        html_lines.append('      max-width: 550px;')
        html_lines.append('      padding: 14px 20px;')
        html_lines.append('      background: #161a22;')
        html_lines.append('      color: #00a8ff;')
        html_lines.append('      text-decoration: none;')
        html_lines.append('      font-family: \'Segoe UI\', sans-serif;')
        html_lines.append('      font-size: 15px;')
        html_lines.append('      font-weight: bold;')
        html_lines.append('      border-radius: 8px;')
        html_lines.append('      border: 1px solid #232730;')
        html_lines.append('      text-align: left;')
        html_lines.append('      box-sizing: border-box;')
        html_lines.append('      transition: all 0.2s ease-in-out;')
        html_lines.append('      margin-bottom: 12px;')
        html_lines.append('    }')
        html_lines.append('    .stream-btn:hover {')
        html_lines.append('      background: #1f2430;')
        html_lines.append('      border-color: #00a8ff;')
        html_lines.append('      color: #fff;')
        html_lines.append('      box-shadow: 0 4px 12px rgba(0, 168, 255, 0.2);')
        html_lines.append('    }')
        html_lines.append('  </style>')
        
        for idx, lnk in enumerate(links_data[:7]):
            label = lnk.get("label", "")
            meta = lnk.get("meta", "")
            direct_url = f"{post_url}?link={idx + 1}"
            
            # Extract Quality
            label_lower = label.lower()
            meta_lower = meta.lower()
            if "hd" in label_lower or "hd" in meta_lower:
                quality = "HD"
            elif "sd" in label_lower or "sd" in meta_lower:
                quality = "SD"
            else:
                quality = "Auto Quality"
                
            # Extract Format
            if "dash" in meta_lower or "mpeg-dash" in meta_lower or "dash" in label_lower:
                fmt = "DASH"
            elif "hls" in meta_lower or "hls" in label_lower:
                fmt = "HLS"
            elif "youtube" in meta_lower or "yt" in meta_lower or "yt" in label_lower:
                fmt = "YT"
            else:
                fmt = "Direct"
                
            # Extract Language
            if "ara" in label_lower or "arabic" in label_lower or "arabic" in meta_lower:
                lang = "ARA"
            elif "esp" in label_lower or "spanish" in label_lower or "spanish" in meta_lower:
                lang = "ESP"
            else:
                lang = "ENG" # Default language
                
            # Construct button label starting with match name
            btn_label = f"{match_name} — Link {idx + 1} | {quality} | {fmt} | {lang}"
            
            # Plain text
            lines.append(f"{idx + 1}. {btn_label}")
            lines.append(f"   Link: {direct_url}\n")
            
            # HTML anchor formatted like a button
            html_lines.append(f'  <a class="stream-btn" href="{direct_url}" target="_blank">{btn_label}</a>')
            
        html_lines.append("</div>")
        
        html_content = "\n".join(html_lines)
        
        # Append the HTML format into the plain text format so they have it there too
        lines.append(f"==================================================")
        lines.append(f" PASTABLE HTML CODE (DARK THEME OPTIMIZED)")
        lines.append(f"==================================================\n")
        lines.append(html_content)
        
        content = "\n".join(lines)
        
        # Write only the match-name HTML format as requested
        os.makedirs("links", exist_ok=True)
        safe_name = match_name.replace(' ', '_').lower()
        with open(os.path.join("links", f"links_{safe_name}.html"), "w", encoding="utf-8") as f:
            f.write(html_content)
            
        print(f"[+] Direct links list successfully written to plain text and HTML list files for {match_name}")
        print(content)
    except Exception as e:
        print(f"[-] Error writing direct links list: {e}")

last_discovery_time = 0

def auto_discover_matches():
    global last_discovery_time
    now_ts = time.time()
    # Run auto-discovery at startup and then every 1 hour (3600 seconds)
    if last_discovery_time > 0 and (now_ts - last_discovery_time) < 3600:
        return
        
    print("[*] Running auto-discovery for upcoming matches...")
    last_discovery_time = now_ts
    
    schedule = load_json(SCHEDULE_FILE) or []
    config = load_json(CONFIG_FILE) or {}
    
    portals = config.get("auto_discover_portals", [
        "https://www.rd9sports.pro/",
        "https://epicsports.mobi/",
        "http://footm.site/",
        "http://footem.site/",
        "https://90live.in/",
        "https://www.90live.org/"
    ])
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    discovered_any = False
    
    name_to_match = {m["match_name"].lower().strip(): m for m in schedule}
    existing_urls = set()
    for m in schedule:
        urls = m.get("source_url", "")
        if isinstance(urls, list):
            for u in urls:
                existing_urls.add(u.lower().strip())
        elif isinstance(urls, str):
            existing_urls.add(urls.lower().strip())
            
    for portal in portals:
        print(f"[*] Scanning portal: {portal}")
        try:
            r = requests.get(portal, headers=headers, timeout=12)
            if r.status_code != 200:
                continue
                
            # Parse links using regex
            links = re.findall(r'href=[\x27\"]([^\x27\"]+)[\x27\"]', r.text)
            
            for url in links:
                resolved_url = urljoin(portal, url)
                u_lower = resolved_url.lower()
                
                # Exclude static/general pages
                if any(p in u_lower for p in ["/privacy", "/contact", "/about", "/disclaimer", "/terms", "/search/label", "feed", "blogger.com", "whatsapp.com", "t.me", "telegram"]):
                    continue
                    
                # Is it a match page? (vs/v in path/text)
                parsed = urlparse(resolved_url)
                path_segment = parsed.path
                if path_segment.lower().endswith(".html"):
                    path_segment = path_segment[:-5]
                elif path_segment.lower().endswith(".htm"):
                    path_segment = path_segment[:-4]
                path_segment = path_segment.replace("-", " ").replace("_", " ")
                
                from generate_player import extract_match_name
                match_name = extract_match_name(path_segment)
                if not match_name:
                    continue
                    
                match_name = match_name.title()
                match_name_lower = match_name.lower().strip()
                resolved_url_lower = resolved_url.lower().strip()
                
                if resolved_url_lower in existing_urls:
                    continue
                    
                if match_name_lower in name_to_match:
                    existing_match = name_to_match[match_name_lower]
                    if existing_match.get("status") == "completed":
                        continue
                        
                    curr_url = existing_match["source_url"]
                    if isinstance(curr_url, list):
                        if resolved_url not in curr_url:
                            curr_url.append(resolved_url)
                    else:
                        if curr_url.lower().strip() != resolved_url_lower:
                            existing_match["source_url"] = [curr_url, resolved_url]
                            
                    existing_urls.add(resolved_url_lower)
                    discovered_any = True
                    print(f"[+] Appended new source URL to existing match {match_name}: {resolved_url}")
                    continue
                    
                print(f"[+] Discovered new match: {match_name} -> {resolved_url}")
                try:
                    mr = requests.get(resolved_url, headers=headers, timeout=10)
                    m_html = mr.text if mr.status_code == 200 else ""
                except Exception:
                    m_html = ""
                    
                # Extract date from page metadata
                base_date = None
                time_match = re.search(r'<time[^>]*datetime=[\x27\"]([^\x27\"]+)[\x27\"]', m_html)
                if time_match:
                    try:
                        base_date = datetime.fromisoformat(time_match.group(1))
                    except Exception:
                        pass
                        
                if not base_date:
                    base_date = datetime.now(timezone.utc)
                    
                # Extract time pattern
                match_dt = None
                time_matches = re.findall(r'\b(\d{1,2})[:.](\d{2})\s*(AM|PM|UTC|GMT|IST|ET)?\b', m_html, re.IGNORECASE)
                for hr_str, min_str, tz in time_matches:
                    try:
                        hr = int(hr_str)
                        mn = int(min_str)
                        if tz:
                            tz = tz.upper()
                            if tz == "PM" and hr < 12:
                                hr += 12
                            elif tz == "AM" and hr == 12:
                                hr = 0
                                
                        if not (0 <= hr <= 23) or not (0 <= mn <= 59):
                            continue
                            
                        dt = base_date.replace(hour=hr, minute=mn, second=0, microsecond=0)
                        if tz in ("UTC", "GMT"):
                            dt = dt.replace(tzinfo=timezone.utc)
                        elif tz == "IST":
                            dt = dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
                        elif tz == "ET":
                            dt = dt.replace(tzinfo=timezone(timedelta(hours=-5)))
                        else:
                            local_tz = datetime.now().astimezone().tzinfo
                            dt = dt.replace(tzinfo=local_tz)
                        match_dt = dt
                        break
                    except Exception:
                        continue
                    
                if not match_dt:
                    # If match page already lists active streams, start immediately
                    if re.search(r'(?i)\b(link\s*\d+|stream\s*\d+|btn\s*\d+)\b', m_html):
                        match_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
                    else:
                        match_dt = datetime.now(timezone.utc) + timedelta(hours=1)
                        
                if match_dt.tzinfo is None:
                    match_dt = match_dt.replace(tzinfo=timezone.utc)
                else:
                    match_dt = match_dt.astimezone(timezone.utc)

                # Skip past matches older than 3 hours ago
                now_utc = datetime.now(timezone.utc)
                if match_dt < now_utc - timedelta(hours=3):
                    print(f"[*] Skipping past match: {match_name} at {match_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}")
                    continue

                time_iso = match_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                
                # Append to schedule
                new_match = {
                    "match_name": match_name,
                    "match_time": time_iso,
                    "source_url": resolved_url,
                    "blogger_post_id": "",
                    "status": "pending"
                }
                schedule.append(new_match)
                name_to_match[match_name_lower] = new_match
                existing_urls.add(resolved_url_lower)
                discovered_any = True
                print(f"[+] Successfully scheduled match: {match_name} at {time_iso}")
                
        except Exception as e:
            print(f"[-] Error scanning portal {portal}: {e}")
            
    if discovered_any:
        save_json(SCHEDULE_FILE, schedule)
        print("[+] Schedule saved after discovery.")

def check_and_run():
    # Run auto-discovery first
    try:
        auto_discover_matches()
    except Exception as e:
        print(f"[-] Auto-discovery failed: {e}")

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
        status = match.get("status", "pending")
        if status == "completed":
            continue

        match_time = parse_time(match["match_time"])
        # Active match window: 15 minutes before kickoff up to 3 hours after
        run_start = match_time - timedelta(minutes=15)
        run_end = match_time + timedelta(hours=3)

        if run_start <= now <= run_end:
            # Check if never run or was run more than 5 minutes ago
            last_run_str = match.get("last_run_time")
            should_run = False
            if not last_run_str:
                should_run = True
            else:
                try:
                    last_run_dt = parse_time(last_run_str)
                    if now - last_run_dt >= timedelta(minutes=5):
                        should_run = True
                except Exception:
                    should_run = True

            if should_run:
                print(f"[*] Starting process/update for active match: {match['match_name']}")
                match["status"] = "processing"
                save_json(SCHEDULE_FILE, schedule) # Save status immediately
                
                # Define output file name
                os.makedirs("players", exist_ok=True)
                temp_output = os.path.join("players", f"player_{match['match_name'].replace(' ', '_').lower()}.html")
                
                # Step 1: Run generate_player.py to crawl and produce player file
                source = match["source_url"]
                if isinstance(source, list):
                    with open("urls.txt", "w", encoding="utf-8") as f:
                        for u in source:
                            f.write(u + "\n")
                    src_arg = ["-f", "urls.txt"]
                    print(f"[*] Scraping multiple source URLs: {source}...")
                else:
                    src_arg = ["-u", source]
                    print(f"[*] Scraping {source}...")
                try:
                    cmd = ["python3", "generate_player.py"] + src_arg + ["-o", temp_output]
                    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    print(f"[+] Scraping successful. Generated {temp_output}")
                except Exception as e:
                    print(f"[-] Scraping failed: {e}")
                    # Revert to pending so we retry in the next loop
                    match["status"] = "pending"
                    changed = True
                    continue

                # Read player HTML content
                if os.path.exists(temp_output):
                    with open(temp_output, "r", encoding="utf-8") as pf:
                        player_html = pf.read()
                else:
                    print(f"[-] Output file {temp_output} not found.")
                    match["status"] = "pending"
                    changed = True
                    continue

                # Step 2: Push/Update on Blogger if OAuth is set up
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
                        # Keep as pending while active so it can update again, but record last run
                        match["status"] = "pending"
                        match["last_run_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                        
                        # Generate the direct links list
                        write_direct_links(match["match_name"], post_url, player_html)
                    except Exception as e:
                        print(f"[-] Blogger upload failed: {e}")
                        match["status"] = "pending"
                else:
                    print("[!] Blogger OAuth not fully configured. Storing player HTML locally only.")
                    match["status"] = "pending"
                    match["last_run_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                
                changed = True

        elif now > run_end:
            # Match has ended, run one final time to finalize links
            print(f"[*] Match active window ended. Performing final crawl for: {match['match_name']}")
            match["status"] = "completed"
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
