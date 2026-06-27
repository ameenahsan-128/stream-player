#!/usr/bin/env python3
import os
import sys
import json
import time
import subprocess
from datetime import datetime, timezone, timedelta
import requests
import time

_orig_request = requests.request

def retrying_request(method, url, **kwargs):
    max_retries = 3
    backoff_factor = 2
    for attempt in range(1, max_retries + 1):
        try:
            response = _orig_request(method, url, **kwargs)
            if response.status_code == 429:
                body = response.text or ""
                is_daily = "per day" in body.lower()
                if not is_daily and attempt < max_retries:
                    sleep_time = 15 * attempt
                    print(f"[!] Hit per-minute 429 rate limit. Retrying in {sleep_time}s (attempt {attempt}/{max_retries})...")
                    time.sleep(sleep_time)
                    continue
            if response.status_code in (502, 503, 504) and attempt < max_retries:
                sleep_time = backoff_factor ** attempt
                print(f"[!] Server error {response.status_code}. Retrying in {sleep_time}s (attempt {attempt}/{max_retries})...")
                time.sleep(sleep_time)
                continue
            return response
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            if attempt < max_retries:
                sleep_time = backoff_factor ** attempt
                print(f"[!] Network error: {exc}. Retrying in {sleep_time}s (attempt {attempt}/{max_retries})...")
                time.sleep(sleep_time)
            else:
                raise

requests.request = retrying_request

import re
import fcntl
from urllib.parse import urlparse, urljoin, urlunparse

from automation_config import (
    get_fixture_api_config,
    get_player_blog_config,
    get_player_slots,
    get_portal_blog_config,
    get_scheduler_config,
    has_oauth,
    load_automation_config,
)
from fixture_manager import schedule_match_allowed, source_record
from lineup_manager import refresh_lineups_for_match
from match_metadata import (
    completion_grace_expired,
    content_hash,
    final_score_text,
    refresh_match_metadata,
    result_is_final,
)
from pipeline_storage import (
    _match_is_completed,
    archive_completed_matches,
    ensure_runtime_dirs,
    load_schedule,
    match_key,
    save_schedule,
    storage_config,
)
from portal_renderer import (
    parse_match_time,
    portal_page_url_matches_match,
    preview_post_title,
    render_preview_post,
    render_streaming_page,
    slugify_match_name,
    split_teams,
    streaming_page_title,
)
from precreate_posts import create_stream_blogger_page, find_existing_blogger_page, is_manual_portal_match, update_blogger_page
from thumbnail_manager import with_thumbnail_src
from generate_player import render_player_html

CONFIG_FILE = "blogger_config.json"
SCHEDULER_STATE_FILE = "scheduler_state.json"

# Default goforsports.net ad script codes
DEFAULT_AD_TOP = """
<div align="center" style="margin: 15px 0;">
  <script type="text/javascript">
    atOptions = {
      'key' : '26752c18ca8361bba098d31342583042',
      'format' : 'iframe',
      'height' : 250,
      'width' : 300,
      'params' : {}
    };
  </script>
  <script type="text/javascript" src="https://www.highperformanceformat.com/26752c18ca8361bba098d31342583042/invoke.js"></script>
</div>
"""

DEFAULT_AD_POPUP = """
<!--Popup Ad Overlay-->
<div id="popup-ad-overlay" style="align-items: center; background: rgba(0, 0, 0, 0.6); display: none; height: 100%; justify-content: center; left: 0px; position: fixed; top: 0px; width: 100%; z-index: 99999;">
  <div style="background: rgb(255, 255, 255); border-radius: 8px; padding: 10px; position: relative;">
    <button onclick="document.getElementById('popup-ad-overlay').style.display='none'" style="background: rgb(51, 51, 51); border: none; color: white; cursor: pointer; font-size: 16px; height: 26px; line-height: 1; position: absolute; right: -12px; top: -12px; width: 26px; border-radius: 50%;">&times;</button>
    <script type="text/javascript">
      atOptions = {
        'key' : '26752c18ca8361bba098d31342583042',
        'format' : 'iframe',
        'height' : 250,
        'width' : 300,
        'params' : {}
      };
    </script>
    <script type="text/javascript" src="https://www.highperformanceformat.com/26752c18ca8361bba098d31342583042/invoke.js"></script>
  </div>
</div>
<script type="text/javascript">
  window.addEventListener('load', function() {
    setTimeout(function() {
      const overlay = document.getElementById('popup-ad-overlay');
      if (overlay) overlay.style.display = 'flex';
    }, 3000);
  });
</script>
<script type="text/javascript" src="https://throughalivemedication.com/45/96/a9/4596a9a27ac7c137dd494fd1f200edbb.js"></script>
<script type="text/javascript" src="https://pl17973087.effectivecpmnetwork.com/45/96/a9/4596a9a27ac7c137dd494fd1f200edbb.js"></script>
<script type="text/javascript" src="https://pl17973277.effectivecpmnetwork.com/66/f1/17/66f11775fe2744312299821ac71b38f1.js"></script>
"""

def is_internet_available():
    """Check if the system has active internet connectivity by hitting a reliable domain."""
    import socket
    try:
        # Connect to a reliable DNS server or public address
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except socket.error:
        return False


def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(filepath, data):
    parent = os.path.dirname(os.path.abspath(filepath))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_scheduler_state(config=None):
    state = load_json(storage_config(config).get("runtime_state_file"))
    return state if isinstance(state, dict) else {}


def save_scheduler_state(state, config=None):
    save_json(storage_config(config).get("runtime_state_file"), state)

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

def html_has_image(html_content):
    return bool(re.search(r"<img\b", html_content or "", flags=re.IGNORECASE))


def extract_first_image_html(html_content):
    match = re.search(r"<img\b[^>]*>", html_content or "", flags=re.IGNORECASE)
    return match.group(0) if match else ""


def preserve_existing_post_thumbnail(config, access_token, post_id, html_content):
    if html_has_image(html_content):
        return html_content

    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/{post_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(url, headers=headers, params={"fields": "content"}, timeout=20)
        response.raise_for_status()
    except Exception as e:
        print(f"[!] Existing preview thumbnail lookup failed; continuing without preserve: {e}")
        return html_content

    image_html = extract_first_image_html((response.json() or {}).get("content", ""))
    if not image_html:
        return html_content

    preserved = (
        '<div style="text-align:center; margin:12px auto 10px; max-width:760px;">'
        f'{image_html}'
        '</div>'
    )
    marker = '<a name="more"></a>'
    if marker in html_content:
        return html_content.replace(marker, f"{preserved}\n{marker}", 1)
    return f"{preserved}\n{html_content}"


def update_blogger_post(config, access_token, post_id, title, html_content, published=None, preserve_existing_thumbnail=False):
    blog_id = config.get("blog_id")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    if preserve_existing_thumbnail:
        html_content = preserve_existing_post_thumbnail(config, access_token, post_id, html_content)
    
    # Try as a Post first
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/{post_id}"
    payload = {
        "kind": "blogger#post",
        "id": post_id,
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    if published:
        payload["published"] = published
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

def create_blogger_post(config, access_token, title, html_content, published=None):
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
    if published:
        payload["published"] = published
    response = requests.post(url, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    res_data = response.json()
    return res_data.get("id"), res_data.get("url")

def parse_time(time_str):
    return parse_match_time(time_str)

def write_direct_links(match_name, post_url, player_html, config=None):
    # Regex to extract the STREAM_LINKS list
    match_data = re.search(r'const STREAM_LINKS\s*=\s*(\[.*?\]);', player_html, re.DOTALL)
    if not match_data:
        return False
    try:
        links_data = json.loads(match_data.group(1))
        if not links_data:
            print(f"[!] No STREAM_LINKS entries found for {match_name}; links HTML was not written.")
            return False
        
        # 1. Plain text format
        lines = []
        lines.append(f"==================================================")
        lines.append(f" DIRECT STREAM LINKS FOR: {match_name.upper()}")
        lines.append(f"==================================================\n")
        
        # 2. HTML format (clean list of paragraph links similar to RD9 Sports, styled for dark backgrounds)
        html_lines = []
        html_lines.append('<div style="font-family:\'Segoe UI\',Roboto,Helvetica,sans-serif; max-width:650px; margin: 20px auto; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 0 10px;">')
        html_lines.append('  <style>')
        html_lines.append('    .stream-btn {')
        html_lines.append('      display: flex;')
        html_lines.append('      flex-direction: column;')
        html_lines.append('      align-items: center;')
        html_lines.append('      justify-content: center;')
        html_lines.append('      width: 100%;')
        html_lines.append('      max-width: 550px;')
        html_lines.append('      margin-bottom: 15px;')
        html_lines.append('      padding: 16px 24px;')
        html_lines.append('      background: linear-gradient(135deg, #e63946 0%, #b81d24 100%);')
        html_lines.append('      color: #ffffff;')
        html_lines.append('      text-decoration: none;')
        html_lines.append('      border-radius: 12px;')
        html_lines.append('      border: 1px solid #ff4d5a;')
        html_lines.append('      box-shadow: 0 4px 15px rgba(230, 57, 70, 0.3);')
        html_lines.append('      box-sizing: border-box;')
        html_lines.append('      transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);')
        html_lines.append('      cursor: pointer;')
        html_lines.append('    }')
        html_lines.append('    .stream-btn:hover {')
        html_lines.append('      background: linear-gradient(135deg, #ff4d5a 0%, #e63946 100%);')
        html_lines.append('      border-color: #ff808b;')
        html_lines.append('      transform: translateY(-2px);')
        html_lines.append('      box-shadow: 0 8px 25px rgba(230, 57, 70, 0.5);')
        html_lines.append('    }')
        html_lines.append('    .stream-btn:active {')
        html_lines.append('      transform: translateY(1px);')
        html_lines.append('      box-shadow: 0 2px 10px rgba(230, 57, 70, 0.3);')
        html_lines.append('    }')
        html_lines.append('    .stream-title {')
        html_lines.append('      font-size: 16px;')
        html_lines.append('      font-weight: 700;')
        html_lines.append('      letter-spacing: 0.5px;')
        html_lines.append('      margin-bottom: 6px;')
        html_lines.append('      text-transform: uppercase;')
        html_lines.append('      color: #ffffff;')
        html_lines.append('      text-shadow: 0 1px 2px rgba(0,0,0,0.2);')
        html_lines.append('      text-align: center;')
        html_lines.append('    }')
        html_lines.append('    .stream-subtitle {')
        html_lines.append('      font-size: 13px;')
        html_lines.append('      font-weight: 600;')
        html_lines.append('      color: #00ff88;')
        html_lines.append('      letter-spacing: 0.5px;')
        html_lines.append('      text-transform: uppercase;')
        html_lines.append('      text-align: center;')
        html_lines.append('    }')
        html_lines.append('  </style>')
        
        for idx, lnk in enumerate(links_data[:7]):
            label = lnk.get("label", "")
            meta = lnk.get("meta", "")
            badges = lnk.get("badges", [])
            lnk_type = lnk.get("type", "")
            height = lnk.get("height")
            latency_ms = lnk.get("latencyMs")
            import time
            direct_url = f"{post_url}?link={idx + 1}&cb={int(time.time())}"

            # --- Quality: use height from probe data first, then badges/text ---
            if height and isinstance(height, (int, float)):
                if height >= 1080:
                    quality = "1080p HD"
                elif height >= 720:
                    quality = "720p HD"
                elif height >= 480:
                    quality = "480p SD"
                else:
                    quality = f"{int(height)}p"
            elif "hd" in badges or "hd" in label.lower() or "hd" in meta.lower():
                quality = "HD"
            elif "sd" in badges or "sd" in label.lower() or "sd" in meta.lower():
                quality = "SD"
            elif "auto" in badges or lnk_type in ("dash", "hls"):
                quality = "Auto Quality"
            else:
                quality = "Auto Quality"

            # --- Format: use actual stream type first ---
            type_fmt_map = {"dash": "DASH", "hls": "HLS", "native": "MP4", "iframe": "Embed"}
            if lnk_type in type_fmt_map:
                fmt = type_fmt_map[lnk_type]
            elif "dash" in badges or "mpeg-dash" in meta.lower():
                fmt = "DASH"
            elif "hls" in badges or "hls" in meta.lower():
                fmt = "HLS"
            elif "mp4" in badges:
                fmt = "MP4"
            elif "iframe" in badges or "embed" in meta.lower():
                fmt = "Embed"
            else:
                fmt = "Stream"

            # --- Language: from badges then text ---
            label_lower = label.lower()
            meta_lower = meta.lower()
            if "ara" in badges or "arabic" in label_lower or "arabic" in meta_lower:
                lang = "ARA"
            elif "esp" in badges or "spanish" in label_lower or "spanish" in meta_lower:
                lang = "ESP"
            elif "eng" in badges or "english" in label_lower:
                lang = "ENG"
            else:
                lang = "ENG"  # Default

            # --- Latency tag ---
            latency_tag = f" · {latency_ms}ms" if latency_ms is not None else ""

            # Construct button elements
            title_text = f"{match_name} — Link {idx + 1}"
            
            iphone_suffix = ""
            if fmt == "HLS" or "ios" in badges:
                iphone_suffix = " · 🍎 Works on iPhone"
                
            subtitle_text = f"{quality} · {fmt} · {lang}{latency_tag}{iphone_suffix}"

            # Plain text representation
            btn_label = f"{title_text} | {subtitle_text}"
            lines.append(f"{idx + 1}. {btn_label}")
            lines.append(f"   Link: {direct_url}\n")

            # HTML anchor formatted like a button with title and subtitle
            html_lines.append(f'  <a class="stream-btn" href="{direct_url}" target="_blank">')
            html_lines.append(f'    <span class="stream-title">{title_text}</span>')
            html_lines.append(f'    <span class="stream-subtitle">{subtitle_text}</span>')
            html_lines.append(f'  </a>')
            
        html_lines.append("</div>")
        
        html_content = "\n".join(html_lines)
        
        # Append the HTML format into the plain text format so they have it there too
        lines.append(f"==================================================")
        lines.append(f" PASTABLE HTML CODE (DARK THEME OPTIMIZED)")
        lines.append(f"==================================================\n")
        lines.append(html_content)
        
        content = "\n".join(lines)
        
        # Write only the match-name HTML format as requested
        paths = ensure_runtime_dirs(config or load_automation_config())
        os.makedirs(paths["links_dir"], exist_ok=True)
        safe_name = slugify_match_name(match_name)
        with open(os.path.join(paths["links_dir"], f"links_{safe_name}.html"), "w", encoding="utf-8") as f:
            f.write(html_content)
            
        # Write full scraped link details (JSON format) to a separate folder
        os.makedirs(paths["diagnostics_dir"], exist_ok=True)
        with open(os.path.join(paths["diagnostics_dir"], f"{safe_name}_details.json"), "w", encoding="utf-8") as f:
            json.dump(links_data, f, indent=2)
            
        print(f"[+] Direct links list successfully written to plain text and HTML list files for {match_name}")
        print(content)
        return True
    except Exception as e:
        print(f"[-] Error writing direct links list: {e}")
        return False


def extract_stream_links(player_html):
    match_data = re.search(r'const STREAM_LINKS\s*=\s*(\[.*?\]);', player_html, re.DOTALL)
    if not match_data:
        return []
    try:
        links = json.loads(match_data.group(1))
    except Exception:
        return []
    return [link for link in links if link.get("url")]


def links_html_path(match_name, config=None):
    return os.path.join(storage_config(config).get("links_dir"), f"links_{slugify_match_name(match_name)}.html")


def links_html_has_buttons(html):
    return bool(re.search(r'<a\b[^>]*\bhref=["\']https?://[^"\']+["\'][^>]*>', html or "", flags=re.IGNORECASE))


def read_links_html(match_name, config=None):
    path = links_html_path(match_name, config)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as lf:
        html = lf.read()
    if not links_html_has_buttons(html):
        print(f"[!] Links HTML has no real stream anchors: {path}")
        return ""
    return html


def write_pregenerated_links(match_name, post_url, config=None, link_count=4):
    """Generate generic portal link buttons before the deep scrape completes.

    Creates N styled buttons (Link 1 - HD, Link 2 - HD, ...) that point to
    ``post_url?link=N`` so users can navigate to the player page immediately
    when the active window opens.  The portal is updated again with real
    metadata after the scrape finishes.
    """
    import time as _time
    html_lines = []
    html_lines.append('<div style="font-family:\'Segoe UI\',Roboto,Helvetica,sans-serif; max-width:650px; margin: 20px auto; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 0 10px;">')
    html_lines.append('  <style>')
    html_lines.append('    .stream-btn { display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; max-width:550px; margin-bottom:15px; padding:16px 24px; background:linear-gradient(135deg,#e63946 0%,#b81d24 100%); color:#fff; text-decoration:none; border-radius:12px; border:1px solid #ff4d5a; box-shadow:0 4px 15px rgba(230,57,70,0.3); box-sizing:border-box; transition:all .3s cubic-bezier(.25,.8,.25,1); cursor:pointer; }')
    html_lines.append('    .stream-btn:hover { background:linear-gradient(135deg,#ff4d5a 0%,#e63946 100%); border-color:#ff808b; transform:translateY(-2px); box-shadow:0 8px 25px rgba(230,57,70,0.5); }')
    html_lines.append('    .stream-btn:active { transform:translateY(1px); box-shadow:0 2px 10px rgba(230,57,70,0.3); }')
    html_lines.append('    .stream-title { font-size:16px; font-weight:700; letter-spacing:.5px; margin-bottom:6px; text-transform:uppercase; color:#fff; text-shadow:0 1px 2px rgba(0,0,0,0.2); text-align:center; }')
    html_lines.append('    .stream-subtitle { font-size:13px; font-weight:600; color:#00ff88; letter-spacing:.5px; text-transform:uppercase; text-align:center; }')
    html_lines.append('  </style>')

    cb = int(_time.time())
    for idx in range(1, link_count + 1):
        direct_url = f"{post_url}?link={idx}&cb={cb}"
        title = f"{match_name} — Link {idx}"
        subtitle = "HD · Embed · ENG"
        html_lines.append(f'  <a class="stream-btn" href="{direct_url}" target="_blank">')
        html_lines.append(f'    <span class="stream-title">{title}</span>')
        html_lines.append(f'    <span class="stream-subtitle">{subtitle}</span>')
        html_lines.append(f'  </a>')

    html_lines.append('</div>')
    html_content = "\n".join(html_lines)

    # Write to links dir so portal update can read it
    paths = ensure_runtime_dirs(config or load_automation_config())
    os.makedirs(paths["links_dir"], exist_ok=True)
    safe_name = slugify_match_name(match_name)
    with open(os.path.join(paths["links_dir"], f"links_{safe_name}.html"), "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[+] Pre-generated {link_count} generic portal links for {match_name}")
    return html_content


def active_window(match, scheduler_config):
    match_time = parse_time(match["match_time"])
    start_offset = int(scheduler_config.get("active_window_start_minutes", 15))
    end_hours = float(scheduler_config.get("active_window_end_hours", 3))
    return match_time - timedelta(minutes=start_offset), match_time + timedelta(hours=end_hours), match_time


def scrape_cooldown_minutes(now, match_time, scheduler_config):
    if now < match_time:
        return int(scheduler_config.get("pre_kickoff_cooldown_minutes", 1))

    fast_window = int(scheduler_config.get("post_kickoff_fast_window_minutes", 20))
    if fast_window > 0 and now <= match_time + timedelta(minutes=fast_window):
        return int(scheduler_config.get("post_kickoff_fast_cooldown_minutes", 5))

    return int(scheduler_config.get("post_kickoff_cooldown_minutes", 10))


def occupied_player_slots(schedule, current_match, now, scheduler_config):
    occupied = set()
    for other in schedule:
        if other is current_match or other.get("status") == "completed":
            continue
        slot_id = other.get("player_slot_id")
        if not slot_id:
            continue
        try:
            run_start, run_end, _ = active_window(other, scheduler_config)
        except Exception:
            continue
        if run_start <= now <= run_end:
            occupied.add(slot_id)
    return occupied


def select_player_slot(match, schedule, slots, now, scheduler_config):
    if not slots:
        return None
    slot_by_id = {slot["id"]: slot for slot in slots}
    current_slot_id = match.get("player_slot_id")
    if current_slot_id in slot_by_id:
        return slot_by_id[current_slot_id]

    occupied = occupied_player_slots(schedule, match, now, scheduler_config)
    for slot in slots:
        if slot["id"] not in occupied:
            return slot
    return None


def dedicated_player_post_id(match, slots):
    post_id = str(match.get("blogger_post_id") or "").strip()
    if not post_id or post_id.startswith("YOUR_"):
        return ""
    slot_post_ids = {str(slot.get("post_id") or "").strip() for slot in slots or []}
    if post_id in slot_post_ids:
        return ""
    return post_id


def record_content_hash(match, key, html):
    hashes = match.get("content_hashes") if isinstance(match.get("content_hashes"), dict) else {}
    hashes[key] = content_hash(html)
    match["content_hashes"] = hashes


def update_portal_match_page(automation_config, new_config, new_token, match, state, links_html="", schedule=None):
    if is_manual_portal_match(match):
        print(f"[*] Skipping manual portal update for {match['match_name']} as state={state}.")
        return match.get("new_blogger_page_url") or match.get("new_blogger_post_url")

    render_match = with_thumbnail_src(automation_config, new_config, match)
    title = streaming_page_title(render_match, new_config)
    page_html = render_streaming_page(new_config, render_match, state=state, links_html=links_html)
    page_id = str(match.get("new_blogger_page_id") or "").strip()

    # If the stored page URL no longer matches this match, discard it and
    # create a correct one. Blogger page URLs cannot be changed via API.
    if page_id and match.get("new_blogger_page_url"):
        if not portal_page_url_matches_match(match["new_blogger_page_url"], match["match_name"]):
            print(f"[!] Stored portal page URL does not match {match['match_name']}: {match['new_blogger_page_url']}")
            print(f"[*] Will search/create a new portal page with the correct URL slug.")
            page_id = ""
            match["new_blogger_page_id"] = ""
            match["new_blogger_page_url"] = ""

    if page_id and not page_id.startswith("YOUR_"):
        print(f"[*] Updating portal Page {page_id} as state={state}...")
        page_url = update_blogger_page(new_config, new_token, page_id, title, page_html)
    else:
        print("[*] Portal Page ID missing; searching or creating the canonical streaming Page...")
        exclude_page_ids = []
        if schedule:
            exclude_page_ids = [
                other.get("new_blogger_page_id")
                for other in schedule
                if other.get("fixture_id") != match.get("fixture_id") and other.get("new_blogger_page_id")
            ]
        found_id, found_url = find_existing_blogger_page(new_config, new_token, title, match["match_name"], exclude_ids=exclude_page_ids)
        if found_id:
            page_id = found_id
            if found_url:
                match["new_blogger_page_url"] = found_url
                render_match = with_thumbnail_src(automation_config, new_config, match)
                title = streaming_page_title(render_match, new_config)
                page_html = render_streaming_page(new_config, render_match, state=state, links_html=links_html)
            page_url = update_blogger_page(new_config, new_token, page_id, title, page_html)
        else:
            page_id, page_url = create_stream_blogger_page(new_config, new_token, render_match, title, page_html)
        match["new_blogger_page_id"] = page_id

    match["new_blogger_page_url"] = page_url
    record_content_hash(match, "stream_page", page_html)
    return page_url


def cleanup_completed_portal_pages(schedule, automation_config, new_config, now):
    """
    Ensure all completed matches in the active schedule have their portal pages
    correctly transitioned to the 'ended' state and their preview posts put down
    before they are archived.
    """
    scheduler_config = get_scheduler_config(automation_config)
    completed = [m for m in schedule if _match_is_completed(m, now, scheduler_config)]
    if not completed:
        return

    portal_updates_enabled = has_oauth(new_config)
    if not portal_updates_enabled:
        return

    try:
        print(f"[*] Found {len(completed)} completed match(es) to clean up on Blogger portal...")
        token = get_access_token(new_config)
        for match in completed:
            # 1. Update portal page to ended state
            if not match.get("new_blog_ended_set"):
                print(f"  [*] Transitioning portal page to ended state for: {match.get('match_name')}")
                try:
                    update_portal_match_page(automation_config, new_config, token, match, "ended", schedule=schedule)
                    match["new_blog_ended_set"] = True
                    match["new_blog_iframe_set"] = False
                    match["new_blog_prepare_set"] = False
                except Exception as e:
                    print(f"  [-] Portal ended-state update failed for {match.get('match_name')}: {e}")

            # 2. Put down preview post
            post_id = str(match.get("new_blogger_post_id") or "").strip()
            if post_id and not match.get("new_blog_post_put_down"):
                print(f"  [*] Putting down preview post {post_id} for: {match.get('match_name')}")
                try:
                    render_match = with_thumbnail_src(automation_config, new_config, match)
                    post_html = render_preview_post(new_config, render_match)
                    post_url = update_blogger_post(
                        new_config,
                        token,
                        post_id,
                        preview_post_title(render_match, new_config),
                        post_html,
                        published="2000-01-01T00:00:00Z",
                        preserve_existing_thumbnail=True,
                    )
                    match["new_blogger_post_url"] = post_url
                    match["new_blog_post_put_down"] = True
                except Exception as e:
                    print(f"  [-] Preview post put-down failed for {match.get('match_name')}: {e}")
    except Exception as e:
        print(f"[-] Failed to cleanup completed portal pages: {e}")



def clean_generated_files(schedule, paths):
    """
    Purge generated files (players, links, thumbnails, scraped details)
    and daily blog html/images that do not belong to matches currently
    in the active schedule.
    """
    try:
        import os
        import re
        import glob
        from datetime import datetime, timezone
        from portal_renderer import slugify_match_name
        
        # Keep set of slugs of matches in the active schedule
        active_slugs = {slugify_match_name(m.get("match_name") or "") for m in schedule if m.get("match_name")}
        
        # 1. Clean player HTML files
        players_dir = paths.get("players_dir")
        if players_dir and os.path.isdir(players_dir):
            for filename in os.listdir(players_dir):
                if filename.startswith("player_") and filename.endswith(".html"):
                    slug = filename[7:-5]
                    if slug not in active_slugs:
                        filepath = os.path.join(players_dir, filename)
                        try:
                            os.remove(filepath)
                            print(f"[*] Comprehensive cleanup: removed stale player file: {filepath}")
                        except Exception as e:
                            print(f"[!] Cleanup error for {filepath}: {e}")
                            
        # 2. Clean links HTML files
        links_dir = paths.get("links_dir")
        if links_dir and os.path.isdir(links_dir):
            for filename in os.listdir(links_dir):
                if filename.startswith("links_") and filename.endswith(".html"):
                    slug = filename[6:-5]
                    if slug not in active_slugs:
                        filepath = os.path.join(links_dir, filename)
                        try:
                            os.remove(filepath)
                            print(f"[*] Comprehensive cleanup: removed stale links file: {filepath}")
                        except Exception as e:
                            print(f"[!] Cleanup error for {filepath}: {e}")
                            
        # 3. Clean thumbnail files
        thumbnails_dir = paths.get("thumbnails_dir")
        if thumbnails_dir and os.path.isdir(thumbnails_dir):
            for filename in os.listdir(thumbnails_dir):
                if filename.startswith("thumb_") and filename.endswith(".jpg"):
                    slug = filename[6:-4]
                    if slug not in active_slugs:
                        filepath = os.path.join(thumbnails_dir, filename)
                        try:
                            os.remove(filepath)
                            print(f"[*] Comprehensive cleanup: removed stale thumbnail: {filepath}")
                        except Exception as e:
                            print(f"[!] Cleanup error for {filepath}: {e}")
                            
        # 4. Clean diagnostics (crawl details)
        diag_dir = paths.get("diagnostics_dir")
        if diag_dir and os.path.isdir(diag_dir):
            for filename in os.listdir(diag_dir):
                # Check if any active match slug is in the filename
                if not any(slug in filename for slug in active_slugs):
                    filepath = os.path.join(diag_dir, filename)
                    try:
                        os.remove(filepath)
                        print(f"[*] Comprehensive cleanup: removed stale diagnostic: {filepath}")
                    except Exception as e:
                        print(f"[!] Cleanup error for {filepath}: {e}")
                        
        # 5. Clean legacy daily blog files older than 2 days
        data_dir = paths.get("data_dir")
        if data_dir and os.path.isdir(data_dir):
            now_dt = datetime.now(timezone.utc)
            for pattern in ("daily_blog_preview_*.html", "daily_blog_thumb_*.jpg"):
                for filepath in glob.glob(os.path.join(data_dir, pattern)):
                    filename = os.path.basename(filepath)
                    date_match = re.search(r"\d{4}-\d{2}-\d{2}", filename)
                    if date_match:
                        try:
                            file_date = datetime.strptime(date_match.group(0), "%Y-%m-%d").date()
                            if (now_dt.date() - file_date).days > 2:
                                os.remove(filepath)
                                print(f"[*] Comprehensive cleanup: removed stale daily blog file: {filepath}")
                        except Exception as e:
                            print(f"[!] Cleanup error for {filepath}: {e}")
    except Exception as e:
        print(f"[!] Error during comprehensive file cleanup: {e}")


# ── URL Template Prediction Engine ──────────────────────────────────────
# Learns slug patterns from domains that worked for past matches,
# then predicts URLs for upcoming matches using those patterns.
# Runs as a fast first layer (~5s) before slow portal scanning (~60-90s).

# Known slug generators: given a match name, produce candidate slugs
# Each entry: (domain_base, slug_function, suffix)
# The slug_function receives (team1, team2) lowercased and returns a slug string.

_SLUG_PATTERNS = [
    # Pattern: {team1}-vs-{team2}.html  (most common — scoopnonstop, footem, sportscorner)
    {
        "domains": [
            "football.scoopnonstop.com",
            "es.footem.in",
            "sportscorner3697.blogspot.com",
        ],
        "base": "/2026/06/",
        "slug": lambda t1, t2: f"{t1}-vs-{t2}",
        "suffix": ".html",
    },
    # Pattern: {team1}-vs-{team2}-live-score-preview.html  (90live)
    {
        "domains": ["90live.yallatvlive.com"],
        "base": "/2026/06/",
        "slug": lambda t1, t2: f"{t1}-vs-{t2}-live-score-preview",
        "suffix": ".html",
    },
    # Pattern: {t1_abbr}-vs-{t2_abbr}.html  (sportstrack — first 3-4 chars)
    {
        "domains": ["sportstrack.yallatvlive.com"],
        "base": "/2026/06/",
        "slug": lambda t1, t2: f"{t1[:4].rstrip('-')}-vs-{t2[:4].rstrip('-')}",
        "suffix": ".html",
    },
]

# Map of match name words to the slug form used by different portals
_TEAM_SLUG_MAP = {
    "cabo verde": "cape-verde",
    "cape verde": "cape-verde",
    "ivory coast": "ivory-coast",
    "new zealand": "new-zealand",
    "south africa": "south-africa",
    "saudi arabia": "saudi-arabia",
    "south korea": "south-korea",
    "costa rica": "costa-rica",
    "united states": "united-states",
    "turkiye": "turkey",
}


def _match_name_to_teams(match_name):
    """Split 'Team1 Vs Team2' into (slug_team1, slug_team2)."""
    parts = re.split(r'\s+vs?\s+', match_name.strip(), maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None, None
    t1 = parts[0].strip().lower()
    t2 = parts[1].strip().lower()
    # Apply slug mapping for multi-word team names
    t1 = _TEAM_SLUG_MAP.get(t1, t1.replace(" ", "-"))
    t2 = _TEAM_SLUG_MAP.get(t2, t2.replace(" ", "-"))
    return t1, t2


def predict_source_urls(match):
    """Generate predicted source URLs for a match using known domain patterns.
    
    Returns list of (url, domain) tuples for URLs that are predicted to exist.
    """
    match_name = match.get("match_name", "")
    t1, t2 = _match_name_to_teams(match_name)
    if not t1 or not t2:
        return []

    candidates = []
    for pattern in _SLUG_PATTERNS:
        slug = pattern["slug"](t1, t2)
        for domain in pattern["domains"]:
            url = f"https://{domain}{pattern['base']}{slug}{pattern['suffix']}"
            candidates.append((url, domain))

    return candidates


def run_source_prediction(schedule, scheduler_config, automation_config):
    """Fast source prediction pass: generate + verify URLs for matches with few sources.
    
    Uses HEAD requests (~3s timeout) to verify predicted URLs exist before adding them.
    Only runs for matches within the source discovery window.
    """
    max_sources = int(scheduler_config.get("max_sources_per_match") or 12)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    now_utc = datetime.now(timezone.utc)
    predicted_any = False

    for match in schedule:
        if match.get("status") in ("completed", "ended"):
            continue

        # Only predict for matches within discovery window
        if not within_source_discovery_window(match, scheduler_config, now_utc):
            continue

        current_sources = source_urls_for_match(match)
        current_count = len(current_sources)

        # Skip if already has enough sources
        if current_count >= 6:
            continue

        existing_canons = {canonical_url(u) for u in current_sources}
        candidates = predict_source_urls(match)
        if not candidates:
            continue

        added = 0
        for url, domain in candidates:
            if canonical_url(url) in existing_canons:
                continue
            if current_count + added >= max_sources:
                break

            # Quick HEAD check to verify URL exists
            try:
                r = requests.head(url, headers=headers, timeout=3, allow_redirects=True)
                if r.status_code == 200:
                    if append_source_to_match(match, url, max_sources, score=3):
                        existing_canons.add(canonical_url(url))
                        added += 1
                        predicted_any = True
                        print(f"[+] Predicted source verified: {match.get('match_name')} <- {domain}")
            except Exception:
                continue

        if added:
            print(f"[+] Added {added} predicted source(s) for {match.get('match_name')} (total: {current_count + added})")

    if predicted_any:
        save_schedule(schedule, automation_config)
        print("[+] Schedule saved after source prediction.")


last_discovery_time = 0

MATCH_ALIASES = {
    "qat": "qatar",
    "qater": "qatar",
    "switz": "switzerland",
    "switzrlnd": "switzerland",
    "swi": "switzerland",
    "sui": "switzerland",
    "scot": "scotland",
    "scotlnd": "scotland",
    "sco": "scotland",
    "tur": "turkiye",
    "turk": "turkiye",
    "turkey": "turkiye",
    "turkye": "turkiye",
    "aus": "australia",
    "austrliaturky": "australia turkiye",
    "bra": "brazil",
    "mor": "morocco",
    "moroco": "morocco",
    "moro": "morocco",
    "para": "paraguay",
    "par": "paraguay",
    "canad": "canada",
    "bosniahrg": "bosnia",
    "safrica": "south africa",
    "korea": "korea",
    "czech": "czechia",
    "czechia": "czechia",
    # Epicsports portal abbreviations
    "irn": "iran",
    "nzlnd": "zealand",
    "nwzlnd": "zealand",
    "ksa": "saudi",
    "urugy": "uruguay",
    "uru": "uruguay",
    "germny": "germany",
    "curcao": "curacao",
    "nthlnds": "netherlands",
    "jpan": "japan",
    "swden": "sweden",
    "tnsia": "tunisia",
    "ivorycst": "ivory",
    "ecdor": "ecuador",
    "sene": "senegal",
    "sengal": "senegal",
    "bel": "belgium",
    "egyp": "egypt",
    "hai": "haiti",
    "esp": "spain",
    "cabo": "verde",
    "cape": "verde",
    "fra": "france",
    "arg": "argentina",
    "alg": "algeria",
    "nor": "norway",
    "irq": "iraq",
    "jor": "jordan",
}

MATCH_STOP_WORDS = {
    "fifa", "world", "cup", "2026", "match", "preview", "live", "info",
    "stream", "streaming", "watch", "score", "lineup", "prediction",
    "predictions", "football", "friendly", "round", "epicsports", "sports",
    "online", "free", "hd", "sd", "vs", "v", "and", "the", "fc", "club"
}

STATIC_LINK_PARTS = [
    "/privacy", "/contact", "/about", "/disclaimer", "/terms", "/dmca", "/search/label",
    "feed", "blogger.com", "whatsapp.com", "t.me", "telegram", "facebook.com",
    "twitter.com", "instagram.com", "pinterest.com", "linkedin.com", "#comment",
    "your_match_link_url"
]


def canonical_url(url):
    parsed = urlparse(url.strip())
    query = parsed.query
    if query == "m=1":
        query = ""
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", query, ""))


def trusted_domain(url, trusted_domains):
    host = urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    for domain in trusted_domains or []:
        domain = domain.lower().strip()
        domain = domain[4:] if domain.startswith("www.") else domain
        if host == domain or host.endswith("." + domain):
            return True
    return False


def normalize_match_tokens(text):
    tokens = []
    raw_words = re.findall(r"[a-z0-9]+", (text or "").lower())
    for raw in raw_words:
        alias_value = MATCH_ALIASES.get(raw, raw)
        for word in str(alias_value).split():
            if word not in MATCH_STOP_WORDS and (len(word) > 2 or word == "usa"):
                tokens.append(word)
    return set(tokens)


def match_source_score(match_name, candidate_text):
    match_tokens = normalize_match_tokens(match_name)
    candidate_tokens = normalize_match_tokens(candidate_text)
    if not match_tokens or not candidate_tokens:
        return 0
    # Exact matches first
    score = len(match_tokens & candidate_tokens)
    # Fuzzy substring matching for abbreviated names
    # e.g. "swden" matches "sweden", "nthlnds" matches "netherlands"
    unmatched_match = match_tokens - candidate_tokens
    unmatched_cand = candidate_tokens - match_tokens
    matched_cand = set()
    for mt in unmatched_match:
        if len(mt) < 3:
            continue
        for ct in unmatched_cand:
            if len(ct) < 3 or ct in matched_cand:
                continue
            # Check if one is a substring of the other (handles abbreviations)
            if mt in ct or ct in mt:
                score += 1
                matched_cand.add(ct)
                break
            # Check consonant-skeleton match for vowel-stripped abbreviations
            # e.g. "swdn" in "sweden" → consonants "swdn" vs "swdn"
            mt_consonants = re.sub(r'[aeiou]', '', mt)
            ct_consonants = re.sub(r'[aeiou]', '', ct)
            if len(mt_consonants) >= 3 and len(ct_consonants) >= 3:
                if mt_consonants == ct_consonants or mt_consonants in ct_consonants or ct_consonants in mt_consonants:
                    score += 1
                    matched_cand.add(ct)
                    break
                # Allow 1-2 char tolerance for longer consonant skeletons
                # e.g. "nthrlnds" vs "nthlnds" (netherlands, missing 'r')
                if len(mt_consonants) >= 5 and len(ct_consonants) >= 5:
                    edits = _simple_edit_distance(mt_consonants, ct_consonants)
                    if edits <= 2:
                        score += 1
                        matched_cand.add(ct)
                        break
    return score


def _simple_edit_distance(a, b):
    """Minimal edit distance for short strings (used for consonant skeleton comparison)."""
    if abs(len(a) - len(b)) > 2:
        return 99
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j-1] + 1, prev[j-1] + (ca != cb)))
        prev = curr
    return prev[-1]


def candidate_text_for_url(url, anchor_text=""):
    parsed = urlparse(url)
    path_text = parsed.path.replace("/", " ").replace("-", " ").replace("_", " ")
    return f"{anchor_text or ''} {path_text}"


def _source_url_path_matches_match(url, match_name):
    """Strict check: the URL path must contain both team name slugs."""
    if not url or not match_name:
        return False
    team1, team2 = split_teams(match_name)
    if not (team1 and team2):
        return False
    try:
        path = urlparse(url).path.lower().replace("_", "-")
    except Exception:
        return False
    slug1 = slugify_match_name(team1).replace("_", "-")
    slug2 = slugify_match_name(team2).replace("_", "-")
    return slug1 in path and slug2 in path


def token_fuzzy_subset(subset, superset):
    """Checks if subset is a fuzzy subset of superset using exact match + consonant skeleton match."""
    for mt in subset:
        matched = False
        for ct in superset:
            if mt == ct:
                matched = True
                break
            mt_consonants = re.sub(r'[aeiou]', '', mt)
            ct_consonants = re.sub(r'[aeiou]', '', ct)
            if len(mt_consonants) >= 3 and len(ct_consonants) >= 3:
                if mt_consonants == ct_consonants or mt_consonants in ct_consonants or ct_consonants in mt_consonants:
                    matched = True
                    break
        if not matched:
            return False
    return True


def source_matches_schedule_item(match, candidate):
    """Return True if a discovered source candidate belongs to this match.

    We require the URL path to contain both team slugs, OR both teams of the
    match to be independently present in the candidate text (via their respective tokens).
    This prevents a partial match (e.g. "Spain vs Saudi Arabia" matching "Cabo Verde vs Saudi Arabia")
    from being attached to the wrong match.
    """
    match_name = match.get("match_name", "")
    candidate_text = candidate.get("text") if isinstance(candidate, dict) else str(candidate or "")
    candidate_url = candidate.get("url") if isinstance(candidate, dict) else ""

    # Fast strict path check
    if _source_url_path_matches_match(candidate_url, match_name):
        return True

    # Two-sided team verification check: split by vs/v to get individual teams
    teams = [t.strip() for t in match_name.lower().split(" vs ") if t.strip()]
    if len(teams) < 2:
        teams = [t.strip() for t in match_name.lower().split(" v ") if t.strip()]

    # If we cannot split into two teams, fall back to simple subset check
    if len(teams) < 2:
        match_tokens = normalize_match_tokens(match_name)
        candidate_tokens = normalize_match_tokens(candidate_text)
        if not match_tokens:
            return False
        return token_fuzzy_subset(match_tokens, candidate_tokens)

    # Resolve token sets for each team independently
    team1_tokens = normalize_match_tokens(teams[0])
    team2_tokens = normalize_match_tokens(teams[1])
    
    if not team1_tokens or not team2_tokens:
        return False

    candidate_tokens = normalize_match_tokens(candidate_text)

    # Check if a team matches the candidate using exact + consonant skeleton fallback
    def team_matches_candidate(team_tokens, candidate_tokens):
        if team_tokens & candidate_tokens:
            return True
        for mt in team_tokens:
            for ct in candidate_tokens:
                if mt == ct:
                    return True
                mt_consonants = re.sub(r'[aeiou]', '', mt)
                ct_consonants = re.sub(r'[aeiou]', '', ct)
                if len(mt_consonants) >= 3 and len(ct_consonants) >= 3:
                    if mt_consonants == ct_consonants or mt_consonants in ct_consonants or ct_consonants in mt_consonants:
                        return True
        return False

    return team_matches_candidate(team1_tokens, candidate_tokens) and team_matches_candidate(team2_tokens, candidate_tokens)


def source_urls_for_match(match):
    value = match.get("source_url", "")
    if isinstance(value, list):
        return [u for u in value if u]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


# Patterns in URL paths that indicate generic non-match content pages
_GENERIC_CONTENT_PATTERNS = [
    "best-fifa", "best-goals", "best-world-cup", "top-10",
    "history", "world-cup-fans", "all-time",
    "privacy", "disclaimer", "about", "contact", "terms",
]


def sanitize_source_urls(source_urls, match_name):
    """Filter out malformed, wrong-match, and generic content source URLs.

    Returns only URLs that are plausibly relevant to the given match.
    """
    if not source_urls or not match_name:
        return source_urls

    clean = []
    match_tokens = normalize_match_tokens(match_name)

    for url in source_urls:
        url = str(url or "").strip()
        if not url:
            continue

        # Reject malformed URLs (e.g. containing brackets from bad scraping)
        if "[" in url or "]" in url or " " in url:
            print(f"  [!] Dropping malformed source URL: {url[:80]}")
            continue

        parsed = urlparse(url)
        path_lower = parsed.path.lower()

        # Reject generic non-match content pages
        if any(pattern in path_lower for pattern in _GENERIC_CONTENT_PATTERNS):
            print(f"  [!] Dropping generic content URL: {url[:80]}")
            continue

        # For URLs that have a clear match-name pattern in the path
        # (e.g. /2026/06/argentina-vs-algeria.html), verify it matches OUR match
        path_text = parsed.path.replace("/", " ").replace("-", " ").replace("_", " ")
        vs_pattern = re.search(r'\bvs?\b', path_text, flags=re.IGNORECASE)
        if vs_pattern:
            mock_match = {"match_name": match_name}
            mock_candidate = {"url": url, "text": path_text}
            if not source_matches_schedule_item(mock_match, mock_candidate):
                print(f"  [!] Dropping wrong-match URL (strict two-sided check): {url[:80]}")
                continue

        clean.append(url)

    return clean


def resolve_source_shortcuts(source_urls):
    """Pre-resolve L1 wrapper pages to their L2/L3 targets.

    Many source pages are simple wrappers with a single "Click Here" button
    linking to an intermediate page (e.g. ``/p/match-info-1.html``) that is
    closer to the actual streams.  Pages may also have plain ``<a>`` links to
    external blogger pages that host stream embeds directly.

    By resolving these *before* the deep scrape, we effectively save 1-2 crawl
    levels and reduce scrape time significantly.

    Returns a new list of URLs with shortcuts resolved.
    """
    from generate_player import (
        fetch_page_html, EpicLinkParser, is_likely_stream_button,
        should_use_browser,
    )
    import re as _re
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resolved = []
    resolved_set = set()  # dedup

    # Domains known to host stream embeds — plain links to these are valuable
    _STREAM_HOST_PATTERNS = (
        "blogspot.com", "github.io", "pages.dev",
        "cloudfront.net", "akamaihd.net",
        "bbvplayline", "kooralive", "albaplayer",
        "huminbird.cn", "akiwat.com", "cinearena",
        "lordatomic", "footem-player", "muesra",
    )

    # Domains to SKIP (ads, analytics, social, theme providers)
    _SKIP_DOMAINS = (
        "google", "facebook", "twitter", "instagram", "whatsapp",
        "youtube.com", "vimeo.com", "telegram", "pinterest",
        "doubleclick", "googlesyndication", "gooyaabi",
        "themexpose", "jsdelivr", "cloudflare", "recaptcha",
        "criteo", "safeframe",
    )

    for url in source_urls:
        try:
            use_browser = should_use_browser(url)
            timeout = 15 if use_browser else 6
            html, ok, _ = fetch_page_html(url, headers=headers, use_browser=use_browser, timeout=timeout)
            if not ok or not html:
                if url not in resolved_set:
                    resolved.append(url)
                    resolved_set.add(url)
                continue

            parser = EpicLinkParser(url)
            parser.feed(html)
            source_domain = urlparse(url).netloc.lower()

            # 1) Collect stream button links (e.g. "Click Here", "Watch Live")
            buttons_found = False
            for item in parser.results:
                if item["tag"] in ("a", "button"):
                    if is_likely_stream_button(item["text"], item["url"], url) and item["url"] != url:
                        btn_url = item["url"]
                        if btn_url not in resolved_set:
                            resolved.append(btn_url)
                            resolved_set.add(btn_url)
                            print(f"  [⚡] Resolved button: {url[:55]} → {btn_url[:55]}")
                            buttons_found = True

            # 2) Collect plain external links to known stream-hosting domains
            all_hrefs = _re.findall(r'href=["\x27](https?://[^"\x27]+)["\x27]', html, _re.I)
            for href in all_hrefs:
                href_domain = urlparse(href).netloc.lower()
                # Skip same-domain, skip junk
                if href_domain == source_domain:
                    continue
                if any(skip in href_domain for skip in _SKIP_DOMAINS):
                    continue
                # Accept if it matches a known stream-hosting pattern
                if any(pat in href_domain or pat in href.lower() for pat in _STREAM_HOST_PATTERNS):
                    # Skip blogger infrastructure (CSS, feeds, comment frames, navbar)
                    path_lower = urlparse(href).path.lower()
                    if any(skip in path_lower for skip in ("/static/", "/feeds/", "/dyn-css/", "/navbar/", "/comment/")):
                        continue
                    if any(skip in href.lower() for skip in ("css_bundle", "authorization.css", "/profile/")):
                        continue
                    if href not in resolved_set:
                        resolved.append(href)
                        resolved_set.add(href)
                        print(f"  [⚡] Resolved ext link: {url[:45]} → {href[:65]}")

            # If no buttons AND no external links found, keep the original
            if not buttons_found and url not in resolved_set:
                resolved.append(url)
                resolved_set.add(url)

        except Exception:
            if url not in resolved_set:
                resolved.append(url)
                resolved_set.add(url)

    if len(resolved) != len(source_urls):
        print(f"  [⚡] Source shortcuts: {len(source_urls)} URLs → {len(resolved)} after resolution")
    return resolved


def append_source_to_match(match, source_url, max_sources, score=0):
    urls = source_urls_for_match(match)
    canon_existing = {canonical_url(u) for u in urls}
    canon_new = canonical_url(source_url)
    if canon_new in canon_existing:
        return False
    if len(urls) >= max_sources:
        return False
    urls.append(source_url)
    match["source_url"] = urls if len(urls) > 1 else urls[0]
    records = match.get("source_records") if isinstance(match.get("source_records"), list) else []
    if not any(canonical_url(record.get("url") or "") == canon_new for record in records if isinstance(record, dict)):
        records.append(source_record(source_url, score=score))
        match["source_records"] = records
    return True


def mark_source_success(match, urls):
    records = match.get("source_records") if isinstance(match.get("source_records"), list) else []
    if not records:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    success_keys = {canonical_url(url) for url in urls or []}
    changed = False
    for record in records:
        if not isinstance(record, dict):
            continue
        if canonical_url(record.get("url") or "") in success_keys:
            record["last_success_at"] = now
            record["fail_count"] = 0
            changed = True
    if changed:
        match["source_records"] = records


def within_source_discovery_window(match, scheduler_config, now_utc):
    hours = scheduler_config.get("source_discovery_hours_before")
    if hours in (None, "", 0, "0"):
        return True
    try:
        hours = float(hours)
        match_time = parse_time(match["match_time"])
        _, run_end, _ = active_window(match, scheduler_config)
    except Exception:
        return True
    if now_utc > run_end:
        return False
    return match_time <= now_utc + timedelta(hours=hours)


def extract_discovery_candidates(portal, html, trusted_domains):
    candidates = []
    seen = set()
    ignored_extensions = {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
        ".css", ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z", ".tar", ".gz",
        ".js", ".json"
    }

    def _url_looks_valid(url):
        # Defensive parsing: reject malformed URLs (e.g. IPv6 literals, brackets)
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if not parsed.scheme.startswith("http"):
            return False
        if not parsed.netloc:
            return False
        if not parsed.path or parsed.path == "/":
            return False
        # Reject literal IPv6 hosts that slipped through (brackets in netloc)
        if "[" in parsed.netloc or "]" in parsed.netloc:
            return False
        return True

    def _is_blocked_url(resolved_url):
        u_lower = resolved_url.lower()
        if any(part in u_lower for part in STATIC_LINK_PARTS):
            return True
        if not trusted_domain(resolved_url, trusted_domains):
            return True
        try:
            parsed = urlparse(resolved_url)
        except Exception:
            return True
        path_lower = parsed.path.lower()
        if any(path_lower.endswith(ext) for ext in ignored_extensions):
            return True
        if any(pattern in path_lower for pattern in _GENERIC_CONTENT_PATTERNS):
            return True
        if "[" in resolved_url or "]" in resolved_url:
            return True
        if "/search?" in resolved_url or "/search/" in path_lower:
            return True
        return False

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    
    # Exclude links from noise sections: sidebars, widgets, popular post lists, headers, footers, navs
    noise_classes = {"sidebar", "widget", "popular-posts", "navigation", "footer", "header", "menu", "nav"}
    noise_tags = {"footer", "header", "nav", "aside"}

    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue

        parent = a.parent
        in_noise_section = False
        while parent:
            if parent.name in noise_tags:
                in_noise_section = True
                break
            cls = parent.get("class") or []
            if isinstance(cls, str):
                cls = [cls]
            if any(any(nc in c.lower() for nc in noise_classes) for c in cls):
                in_noise_section = True
                break
            parent_id = (parent.get("id") or "").lower()
            if any(nc in parent_id for nc in noise_classes):
                in_noise_section = True
                break
            parent = parent.parent

        if in_noise_section:
            continue

        try:
            resolved_url = urljoin(portal, href)
        except Exception:
            continue
        if not _url_looks_valid(resolved_url):
            continue
        if _is_blocked_url(resolved_url):
            continue
        canon = canonical_url(resolved_url)
        if canon in seen:
            continue
        seen.add(canon)

        body = a.get_text()
        body = re.sub(r"\s+", " ", body).strip()

        # Build direct text from the URL path and anchor body only.
        url_text = candidate_text_for_url(resolved_url, "")
        anchor_text = body
        direct_text = f"{url_text} {anchor_text}".strip()

        # Context text extraction
        a_str = str(a)
        link_start = (html or "").find(a_str)
        if link_start != -1:
            context_window = (html or "")[max(0, link_start - 500):link_start]
        else:
            context_window = ""
        context_text = re.sub(r"<[^>]+>", " ", context_window)
        context_text = re.sub(r"\s+", " ", context_text).strip()
        context_text = context_text[-120:] if context_text else ""

        candidates.append({
            "url": resolved_url,
            "text": direct_text,
            "url_text": url_text,
            "anchor_text": anchor_text,
            "context_text": context_text,
        })

    for raw_url in re.findall(r'https?://[^\s\x27"<>]+', html or ""):
        try:
            resolved_url = raw_url.rstrip("),.;")
        except Exception:
            continue
        if not _url_looks_valid(resolved_url):
            continue
        if _is_blocked_url(resolved_url):
            continue
        canon = canonical_url(resolved_url)
        if canon in seen:
            continue
        seen.add(canon)
        url_text = candidate_text_for_url(resolved_url, "")
        candidates.append({
            "url": resolved_url,
            "text": url_text,
            "url_text": url_text,
            "anchor_text": "",
            "context_text": "",
        })
    return candidates


def extract_match_name_from_candidate(candidate_text):
    from generate_player import extract_match_name
    match_name = extract_match_name(candidate_text)
    if match_name:
        return match_name.title()
    return None


def has_active_match_now(schedule=None, scheduler_config=None):
    """Check if any match is currently within its active window."""
    try:
        if schedule is None:
            automation_config = load_automation_config()
            schedule = load_schedule(automation_config)
            scheduler_config = get_scheduler_config(automation_config)
        now = datetime.now(timezone.utc)
        for match in schedule:
            if match.get("status") in ("completed",):
                continue
            try:
                run_start, run_end, _ = active_window(match, scheduler_config)
                if run_start <= now <= run_end:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def auto_discover_matches(force=False, skip_if_active_match=False):
    global last_discovery_time
    now_ts = time.time()
    automation_config = load_automation_config()
    api_config = get_fixture_api_config(automation_config)
    schedule = load_schedule(automation_config)
    scheduler_config = get_scheduler_config(automation_config)
    fixture_first_only = bool(scheduler_config.get("fixture_first_only", True)) or bool(api_config.get("enabled", False))
    allow_auto_create = bool(scheduler_config.get("auto_create_matches_from_discovery", False)) and not fixture_first_only
    interval = int(
        scheduler_config.get("source_refresh_interval_seconds")
        or scheduler_config.get("auto_discover_interval_seconds", 1800)
    )
    state = load_scheduler_state(automation_config)
    persisted_last = float(state.get("last_source_refresh_ts") or 0)
    effective_last = max(float(last_discovery_time or 0), persisted_last)
    if not force and effective_last > 0 and (now_ts - effective_last) < interval:
        return

    # Defer discovery when a match is actively live — prioritize match processing
    if skip_if_active_match and has_active_match_now(schedule, scheduler_config):
        print("[*] Deferring source discovery — active match window detected; prioritizing match processing.")
        return

    print("[*] Running source discovery/refresh for upcoming matches...")
    last_discovery_time = now_ts
    state["last_source_refresh_ts"] = now_ts
    state["last_source_refresh_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_scheduler_state(state, automation_config)
    portals = (
        scheduler_config.get("discovery_portals")
        or scheduler_config.get("auto_discover_portals")
        or []
    )
    trusted_domains = scheduler_config.get("trusted_source_domains") or []
    max_sources = int(scheduler_config.get("max_sources_per_match") or 8)
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    discovered_any = False
    
    existing_urls = set()
    for m in schedule:
        for u in source_urls_for_match(m):
            existing_urls.add(canonical_url(u))
            
    for portal in portals:
        print(f"[*] Scanning portal: {portal}")
        try:
            # Use browser-based fetch for JS-heavy portals (same as generate_player.py)
            from generate_player import fetch_page_html, should_use_browser
            use_browser = should_use_browser(portal)
            timeout = 20 if use_browser else 5
            html, success, method = fetch_page_html(portal, headers=headers, use_browser=use_browser, timeout=timeout)
            if not success or not html:
                continue

            for candidate in extract_discovery_candidates(portal, html, trusted_domains):
                resolved_url = candidate["url"]
                candidate_text = candidate["text"]
                resolved_url_key = canonical_url(resolved_url)

                if resolved_url_key in existing_urls:
                    continue

                matched_existing = None
                best_score = 0
                for existing_match in schedule:
                    if existing_match.get("status") in ("completed", "ended"):
                        continue
                    if fixture_first_only and not schedule_match_allowed(existing_match, api_config):
                        continue
                    try:
                        match_time = parse_time(existing_match["match_time"])
                        now_utc = datetime.now(timezone.utc)
                        if now_utc >= match_time + timedelta(hours=3):
                            continue
                    except Exception:
                        pass
                    if source_matches_schedule_item(existing_match, candidate):
                        score = match_source_score(existing_match.get("match_name", ""), candidate_text)
                        if score > best_score:
                            matched_existing = existing_match
                            best_score = score

                if matched_existing:
                    now_utc = datetime.now(timezone.utc)
                    can_refresh_sources = within_source_discovery_window(matched_existing, scheduler_config, now_utc)
                    if can_refresh_sources and append_source_to_match(matched_existing, resolved_url, max_sources, score=best_score):
                        existing_urls.add(resolved_url_key)
                        discovered_any = True
                        print(f"[+] Appended source URL to {matched_existing['match_name']}: {resolved_url}")
                    continue

                if not allow_auto_create:
                    continue

                match_name = extract_match_name_from_candidate(candidate_text)
                if not match_name:
                    continue

                if any(m.get("match_name", "").lower().strip() == match_name.lower().strip() for m in schedule):
                    continue

                print(f"[+] Discovered new match: {match_name} -> {resolved_url}")
                try:
                    mr = requests.get(resolved_url, headers=headers, timeout=10)
                    m_html = mr.text if mr.status_code == 200 else ""
                except Exception:
                    m_html = ""
                    
                # Extract date from page text or metadata
                base_date = None
                
                # Check for DATE DD - MM - YYYY or DATE DD/MM/YYYY or similar patterns in the HTML text
                date_match = re.search(r'(?i)\bdate\b\s*(?:info)?\s*[:\-\s]*(\d{1,2})\s*[\-\/]\s*(\d{1,2})\s*[\-\/]\s*(\d{4})', m_html)
                if date_match:
                    try:
                        day = int(date_match.group(1))
                        month = int(date_match.group(2))
                        year = int(date_match.group(3))
                        base_date = datetime(year, month, day)
                    except Exception:
                        pass
                
                if not base_date:
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
                    has_stream_buttons = re.search(r'(?i)\b(link\s*\d+|stream\s*\d+|btn\s*\d+)\b', m_html)
                    if has_stream_buttons:
                        match_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
                    else:
                        print(f"[*] Skipping candidate without date/time or stream buttons: {match_name} -> {resolved_url}")
                        continue
                        
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
                existing_urls.add(resolved_url_key)
                discovered_any = True
                print(f"[+] Successfully scheduled match: {match_name} at {time_iso}")
                
        except Exception as e:
            print(f"[-] Error scanning portal {portal}: {e}")
            
    if discovered_any:
        save_schedule(schedule, automation_config)
        print("[+] Schedule saved after discovery.")

    # ── Pre-resolve source shortcuts (L1→L2/L3) ────────────────────────
    # Run during the discovery phase (all day) so by the time the active
    # window opens, source URLs already point to deeper pages.
    try:
        now_utc = datetime.now(timezone.utc)
        shortcuts_changed = False
        for match in schedule:
            if match.get("status") in ("completed", "ended"):
                continue
            if match.get("_shortcuts_resolved"):
                continue
            sources = source_urls_for_match(match)
            if not sources:
                continue
            # Only resolve for matches within the next 8 hours
            try:
                mt = parse_time(match["match_time"])
                if mt - now_utc > timedelta(hours=8) or now_utc >= mt + timedelta(hours=3):
                    continue
            except Exception:
                continue

            resolved = resolve_source_shortcuts(sources)
            # Add any new resolved URLs as additional sources
            new_urls = [u for u in resolved if u not in sources]
            if new_urls:
                added = 0
                for new_url in new_urls:
                    if append_source_to_match(match, new_url, max_sources, score=2):
                        added += 1
                if added:
                    print(f"[⚡] Added {added} pre-resolved L2/L3 source(s) for {match.get('match_name')}")
                    shortcuts_changed = True
            match["_shortcuts_resolved"] = True
            shortcuts_changed = True
        if shortcuts_changed:
            save_schedule(schedule, automation_config)
    except Exception as e:
        print(f"[-] Source shortcut pre-resolution failed (non-fatal): {e}")

def check_and_run():
    # Sync fixtures first if API is enabled
    try:
        automation_config = load_automation_config()
        api_config = get_fixture_api_config(automation_config)
        scheduler_config = get_scheduler_config(automation_config)
        if api_config.get("enabled", False):
            now_ts = time.time()
            state = load_scheduler_state(automation_config)
            interval = int(scheduler_config.get("fixture_sync_interval_seconds") or 3600)
            last_sync = float(state.get("last_fixture_sync_ts") or 0)
            if last_sync <= 0 or (now_ts - last_sync) >= interval:
                print("[*] Running automatic fixture synchronization...")
                try:
                    from sync_fixtures import sync_fixtures
                    sync_fixtures(dry_run=False)
                    state["last_fixture_sync_ts"] = now_ts
                    state["last_fixture_sync_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    save_scheduler_state(state, automation_config)
                except ImportError:
                    print("[-] Could not import sync_fixtures module.")
    except Exception as e:
        print(f"[-] Fixture synchronization failed: {e}")

    # Defer auto-discovery until after match processing (moved below)
    # This avoids blocking active match scraping with 60-90s of portal scanning

    automation_config = load_automation_config()
    schedule = load_schedule(automation_config)
    config = get_player_blog_config(automation_config)
    new_config = get_portal_blog_config(automation_config)
    scheduler_config = get_scheduler_config(automation_config)
    player_slots = get_player_slots(automation_config)
    paths = ensure_runtime_dirs(automation_config)
    if not schedule:
        return

    has_player_oauth = has_oauth(config)
    has_new_oauth = has_oauth(new_config)

    changed = False
    now = datetime.now(timezone.utc)
    cleanup_completed_portal_pages(schedule, automation_config, new_config, now)
    schedule, archived = archive_completed_matches(schedule, automation_config, scheduler_config, now)
    if archived:
        print(f"[*] Archived {len(archived)} completed match(es) before scheduler run.")
        changed = True

    # Run comprehensive file cleanup to remove unnecessary files
    clean_generated_files(schedule, paths)

    # Purge junk source URLs from all pending/active matches
    for match in schedule:
        if match.get("status") in ("completed",):
            continue
        sources = source_urls_for_match(match)
        if sources:
            clean = sanitize_source_urls(sources, match.get("match_name", ""))
            if len(clean) < len(sources):
                removed = len(sources) - len(clean)
                print(f"[*] Purged {removed} junk source URL(s) from {match.get('match_name')}")
                match["source_url"] = clean if len(clean) > 1 else (clean[0] if clean else "")
                changed = True

    # Fast source prediction: instantly generate + verify URLs from known patterns
    # Runs in ~5s vs 60-90s for portal scanning — fills gaps for matches with few sources
    if scheduler_config.get("prediction_enabled", True):
        try:
            run_source_prediction(schedule, scheduler_config, automation_config)
        except Exception as e:
            print(f"[-] Source prediction error (non-fatal): {e}")
    else:
        print("[*] Source prediction is disabled via configuration.")

    # Determine priority teams from configuration to prioritize processing order
    priority_teams = []
    if new_config:
        priority_teams = [
            str(t).strip().lower()
            for t in (
                (new_config.get("prominent_team_priority") or [])
                + (new_config.get("title_priority_teams") or [])
                + (new_config.get("priority_teams") or [])
            )
            if t
        ]

    def get_match_sort_key(m):
        try:
            m_time = parse_time(m["match_time"])
        except Exception:
            m_time = datetime.max.replace(tzinfo=timezone.utc)
        
        has_priority = 0
        m_name_lower = m.get("match_name", "").lower()
        for pt in priority_teams:
            if pt in m_name_lower:
                has_priority = 1
                break
        
        # Sort by match time (ascending), then by priority (descending, so -1 first)
        return (m_time, -has_priority)

    sorted_schedule = sorted(schedule, key=get_match_sort_key)
    for match in sorted_schedule:
        try:
            status = match.get("status", "pending")
            if status == "completed":
                continue

            try:
                run_start, run_end, _ = active_window(match, scheduler_config)
            except Exception:
                run_end = datetime.max.replace(tzinfo=timezone.utc)

            if status in ("review", "failed") and now <= run_end:
                continue
            # Self-heal: if a previous run crashed mid-way and left status=processing,
            # treat it as pending so the scheduler retries instead of freezing.
            if status == "processing":
                print(f"[!] Match '{match.get('match_name')}' found in 'processing' state — resetting to 'pending' for retry.")
                match["status"] = "pending"
                changed = True
                status = "pending"
            match["match_key"] = match.get("match_key") or match_key(match)
            portal_updates_enabled = has_new_oauth and not is_manual_portal_match(match)

            match_time = parse_time(match["match_time"])
            run_start, run_end, _ = active_window(match, scheduler_config)

            if run_start <= now <= run_end:
                fail_count = int(match.get("scrape_fail_count") or 0)
                max_failures = int(scheduler_config.get("max_scrape_failures") or 5)
                if fail_count >= max_failures:
                    print(f"[⚠️] Match {match['match_name']} exceeded safety threshold of {max_failures} failures. Moving to 'review' state.")
                    match["status"] = "review"
                    if portal_updates_enabled:
                        try:
                            new_token = get_access_token(new_config)
                            update_portal_match_page(automation_config, new_config, new_token, match, "preparing", schedule=schedule)
                            match["new_blog_prepare_set"] = True
                        except Exception as e:
                            print(f"[-] Portal preparing-state update failed: {e}")
                    changed = True
                    continue

                cooldown_min = scrape_cooldown_minutes(now, match_time, scheduler_config)

                # Failure backoff: if we have had 3+ consecutive failures, skip until
                # at least (fail_count * cooldown_min) minutes have passed since last run.
                if fail_count >= 3:
                    backoff_min = min(fail_count * cooldown_min, 30)  # cap at 30 min
                    last_run_str = match.get("last_run_time")
                    if last_run_str:
                        try:
                            last_run_dt = parse_time(last_run_str)
                            if now - last_run_dt < timedelta(minutes=backoff_min):
                                print(f"[!] Skipping {match['match_name']} — backoff active ({fail_count} failures, wait {backoff_min} min).")
                                continue
                        except Exception:
                            pass

                last_run_str = match.get("last_run_time")
                should_run = False
                if not last_run_str:
                    should_run = True
                else:
                    try:
                        last_run_dt = parse_time(last_run_str)
                        if now - last_run_dt >= timedelta(minutes=cooldown_min):
                            should_run = True
                    except Exception:
                        should_run = True

                if should_run:
                    print(f"[*] Starting process/update for active match: {match['match_name']}")
                    metadata_changed = False
                    lineups_changed = False
                    try:
                        metadata_changed = refresh_match_metadata(match, scheduler_config, now, active=True)
                        if metadata_changed:
                            print(f"[*] Metadata checked/updated for active match: {match['match_name']}")
                        lineups_changed = refresh_lineups_for_match(match, scheduler_config, now, active=True)
                        if lineups_changed or metadata_changed:
                            if lineups_changed:
                                print(f"[*] Lineup info checked/updated for active match: {match['match_name']}")
                            match["new_blog_prepare_set"] = False
                            post_id = str(match.get("new_blogger_post_id") or "").strip()
                            if portal_updates_enabled and post_id:
                                try:
                                    new_token = get_access_token(new_config)
                                    render_match = with_thumbnail_src(automation_config, new_config, match)
                                    post_html = render_preview_post(new_config, render_match)
                                    post_url = update_blogger_post(
                                        new_config,
                                        new_token,
                                        post_id,
                                        preview_post_title(render_match, new_config),
                                        post_html,
                                        preserve_existing_thumbnail=True,
                                    )
                                    match["new_blogger_post_url"] = post_url
                                    record_content_hash(match, "preview_post", post_html)
                                    print(f"[+] Preview post refreshed with metadata update: {post_url}")
                                except Exception as e:
                                    print(f"[-] Preview post metadata refresh failed: {e}")
                    except Exception as e:
                        print(f"[-] Metadata/lineup refresh failed: {e}")
                    # Stamp last_run_time NOW (before the scrape) so the cooldown window is
                    # measured from when we started, not when we finished.  This prevents
                    # immediate re-triggers if the scrape itself takes longer than cooldown.
                    match["last_run_time"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                    match["status"] = "processing"
                    save_schedule(schedule, automation_config) # Save status immediately
                
                    # Define output file name
                    os.makedirs(paths["players_dir"], exist_ok=True)
                    temp_output = os.path.join(paths["players_dir"], f"player_{slugify_match_name(match['match_name'])}.html")
                
                    # ── Phase 1: Pre-generate with placeholder loading page ──────
                    # On the very first run (no existing player), generate a match-specific
                    # placeholder loading page with an auto-refresh script. This ensures the
                    # user doesn't see empty or wrong match links before the deep scrape finishes.
                    is_first_run = not os.path.exists(temp_output) and not match.get("_pregen_done")
                    if is_first_run:
                        try:
                            placeholder_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Loading Live Stream - {match['match_name']}</title>
  <style>
    body {{
      background-color: #0b0f19;
      color: #ffffff;
      font-family: 'Segoe UI', Roboto, Helvetica, sans-serif;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      text-align: center;
    }}
    .container {{
      max-width: 500px;
      padding: 40px 30px;
      background: rgba(255, 255, 255, 0.03);
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
      backdrop-filter: blur(8px);
    }}
    .spinner {{
      width: 50px;
      height: 50px;
      border: 3px solid rgba(230, 57, 70, 0.1);
      border-radius: 50%;
      border-top-color: #e63946;
      animation: spin 1s ease-in-out infinite;
      margin: 0 auto 20px;
    }}
    @keyframes spin {{
      to {{ transform: rotate(360deg); }}
    }}
    h2 {{
      font-size: 22px;
      margin: 10px 0;
      color: #ffffff;
      font-weight: 700;
    }}
    p {{
      font-size: 14px;
      color: #a0aec0;
      margin-bottom: 25px;
      line-height: 1.5;
    }}
    .badge {{
      display: inline-block;
      padding: 6px 12px;
      background: rgba(230, 57, 70, 0.15);
      color: #ff4d5a;
      border: 1px solid rgba(230, 57, 70, 0.3);
      border-radius: 20px;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
      animation: pulse 1.5s infinite;
    }}
    @keyframes pulse {{
      0% {{ opacity: 0.6; }}
      50% {{ opacity: 1; }}
      100% {{ opacity: 0.6; }}
    }}
  </style>
  <script>
    // Auto-reload every 15 seconds to check if real streams are ready
    setTimeout(function() {{
      window.location.reload();
    }}, 15000);
  </script>
</head>
<body>
  <div class="container">
    <div class="spinner"></div>
    <div class="badge">Live Stream Loading</div>
    <h2>{match['match_name']}</h2>
    <p>We are scanning for the best live stream channels. This page will automatically update with stream buttons once they are verified.</p>
  </div>
</body>
</html>"""
                            
                            cached_player_html = placeholder_html
                            with open(temp_output, "w", encoding="utf-8") as pf:
                                pf.write(cached_player_html)
                            print(f"[+] Phase 1: Pre-populated loading placeholder player for: {match['match_name']}")

                            # Upload the cached player to Blogger immediately
                            pregen_post_url = None
                            if has_player_oauth:
                                try:
                                    token = get_access_token(config)
                                    dedicated_post_id = dedicated_player_post_id(match, player_slots)
                                    if dedicated_post_id:
                                        post_title = match["match_name"] + " Live Stream"
                                        pregen_post_url = update_blogger_post(config, token, dedicated_post_id, post_title, cached_player_html)
                                        match["blogger_post_id"] = dedicated_post_id
                                        match["blogger_post_url"] = pregen_post_url
                                        match["player_slot_url"] = pregen_post_url
                                        print(f"[+] Phase 1: Player page uploaded (cached): {pregen_post_url}")
                                    else:
                                        if config.get("create_dedicated_player_posts", True):
                                            post_title = match["match_name"] + " Live Stream"
                                            post_id, pregen_post_url = create_blogger_post(config, token, post_title, cached_player_html)
                                            match["blogger_post_id"] = post_id
                                            match["blogger_post_url"] = pregen_post_url
                                            match["player_slot_url"] = pregen_post_url
                                            print(f"[+] Phase 1: Player post created (cached): {pregen_post_url}")
                                except Exception as e:
                                    print(f"[-] Phase 1: Player upload failed (will retry after scrape): {e}")

                            # Publish pre-generated portal links immediately
                            pregen_post_url = pregen_post_url or match.get("blogger_post_url") or match.get("player_slot_url")
                            if pregen_post_url and portal_updates_enabled:
                                try:
                                    links_html = write_pregenerated_links(match["match_name"], pregen_post_url, automation_config)
                                    new_token = get_access_token(new_config)
                                    portal_url = update_portal_match_page(automation_config, new_config, new_token, match, "live", links_html=links_html, schedule=schedule)
                                    match["new_blog_iframe_set"] = True
                                    match["new_blog_prepare_set"] = False
                                    print(f"[+] Phase 1: Portal updated with pre-generated links: {portal_url}")
                                except Exception as e:
                                    print(f"[-] Phase 1: Portal pre-generation failed: {e}")

                            match["_pregen_done"] = True
                            save_schedule(schedule, automation_config)
                        except Exception as e:
                            print(f"[-] Phase 1 pre-generation failed (non-fatal): {e}")

                    # ── Phase 2: Deep scrape ────────────────────────────────────
                    # Step 1: Run generate_player.py to crawl and produce player file
                    sources = source_urls_for_match(match)
                    # Sanitize source URLs: remove malformed, wrong-match, and generic content pages
                    if sources:
                        clean_sources = sanitize_source_urls(sources, match.get("match_name", ""))
                        if len(clean_sources) < len(sources):
                            removed = len(sources) - len(clean_sources)
                            print(f"[*] Filtered {removed} junk/irrelevant source URL(s) for {match['match_name']}")
                            sources = clean_sources
                            # Persist the cleaned-up source list
                            match["source_url"] = sources if len(sources) > 1 else (sources[0] if sources else "")
                    if not sources:
                        print(f"[!] No source URLs available yet for {match['match_name']}; waiting for source discovery.")
                        if portal_updates_enabled and not match.get("new_blog_prepare_set") and not match.get("new_blog_iframe_set"):
                            try:
                                new_token = get_access_token(new_config)
                                update_portal_match_page(automation_config, new_config, new_token, match, "preparing", schedule=schedule)
                                match["new_blog_prepare_set"] = True
                            except Exception as e:
                                print(f"[-] Portal preparing-state update failed: {e}")
                        match["status"] = "pending"
                        changed = True
                        continue

                    if len(sources) > 1:
                        urls_path = os.path.join(paths["data_dir"], "urls.txt")
                        with open(urls_path, "w", encoding="utf-8") as f:
                            for u in sources:
                                f.write(u + "\n")
                        src_arg = ["-f", urls_path]
                        print(f"[*] Scraping multiple source URLs: {sources}...")
                    else:
                        src_arg = ["-u", sources[0]]
                        print(f"[*] Scraping {sources[0]}...")
                    try:
                        cmd = ["python3", "generate_player.py"] + src_arg + ["-o", temp_output, "-t", match["match_name"]]
                        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=240)
                        print(f"[+] Scraping successful. Generated {temp_output}")
                        if res.stdout:
                            print(res.stdout)
                        if res.stderr:
                            print(res.stderr)
                        match["scrape_fail_count"] = 0  # Reset failure counter on success
                        mark_source_success(match, sources)
                    except subprocess.CalledProcessError as e:
                        print(f"[-] Scraping failed with exit code {e.returncode}")
                        if e.stdout:
                            print("=== SCRAPER STDOUT ===")
                            print(e.stdout)
                        if e.stderr:
                            print("=== SCRAPER STDERR ===")
                            print(e.stderr)
                        # Increment failure counter for backoff logic
                        if is_internet_available():
                            match["scrape_fail_count"] = int(match.get("scrape_fail_count") or 0) + 1
                        else:
                            print("[⚠️] System offline — skipping failure count increment.")
                        print(f"[!] Scrape fail count for {match['match_name']}: {match['scrape_fail_count']}")
                        match["status"] = "pending"
                        changed = True
                        continue
                    except Exception as e:
                        print(f"[-] Scraping failed: {e}")
                        if is_internet_available():
                            match["scrape_fail_count"] = int(match.get("scrape_fail_count") or 0) + 1
                        else:
                            print("[⚠️] System offline — skipping failure count increment.")
                        print(f"[!] Scrape fail count for {match['match_name']}: {match['scrape_fail_count']}")
                        match["status"] = "pending"
                        changed = True
                        continue

                    # Read player HTML content
                    if os.path.exists(temp_output):
                        with open(temp_output, "r", encoding="utf-8") as pf:
                            player_html = pf.read()
                    else:
                        print(f"[-] Output file {temp_output} not found.")
                        if is_internet_available():
                            match["scrape_fail_count"] = int(match.get("scrape_fail_count") or 0) + 1
                        else:
                            print("[⚠️] System offline — skipping failure count increment.")
                        match["status"] = "pending"
                        changed = True
                        continue

                    real_stream_links = extract_stream_links(player_html)
                    if not real_stream_links:
                        print(f"[!] No playable stream links resolved for {match['match_name']}; skipping player-blog upload.")
                        if is_internet_available():
                            match["scrape_fail_count"] = int(match.get("scrape_fail_count") or 0) + 1
                        else:
                            print("[⚠️] System offline — skipping failure count increment.")
                        print(f"[!] Scrape fail count for {match['match_name']}: {match['scrape_fail_count']}")
                        # Only set preparing state if the portal page hasn't been set to live yet
                        # (avoid overwriting live links with "preparing" message)
                        if portal_updates_enabled and not match.get("new_blog_prepare_set") and not match.get("new_blog_iframe_set"):
                            try:
                                new_token = get_access_token(new_config)
                                update_portal_match_page(automation_config, new_config, new_token, match, "preparing", schedule=schedule)
                                match["new_blog_prepare_set"] = True
                            except Exception as e:
                                print(f"[-] Portal preparing-state update failed: {e}")
                        match["status"] = "pending"
                        changed = True
                        continue

                    # Step 2: Push/Update on the configured player slot if OAuth is set up
                    post_url = None
                    if has_player_oauth:
                        try:
                            print(f"[*] Fetching access token...")
                            token = get_access_token(config)
                            dedicated_post_id = dedicated_player_post_id(match, player_slots)
                            if dedicated_post_id:
                                post_title = match["match_name"] + " Live Stream"
                                print(f"[*] Updating dedicated player post {dedicated_post_id}...")
                                post_url = update_blogger_post(config, token, dedicated_post_id, post_title, player_html)
                                print(f"[+] Dedicated player post updated successfully! URL: {post_url}")
                                match["blogger_post_id"] = dedicated_post_id
                                match["blogger_post_url"] = post_url
                                match["player_slot_url"] = post_url
                                match.pop("player_slot_id", None)
                                match.pop("player_slot_post_id", None)
                            else:
                                if config.get("create_dedicated_player_posts", True):
                                    try:
                                        post_title = match["match_name"] + " Live Stream"
                                        print(f"[*] Creating dedicated player post for {match['match_name']}...")
                                        post_id, post_url = create_blogger_post(config, token, post_title, player_html)
                                        print(f"[+] Dedicated player post created successfully! URL: {post_url}")
                                        match["blogger_post_id"] = post_id
                                        match["blogger_post_url"] = post_url
                                        match["player_slot_url"] = post_url
                                        match.pop("player_slot_id", None)
                                        match.pop("player_slot_post_id", None)
                                        match.pop("iframe_embed_code", None)
                                        slot = None
                                    except Exception as e:
                                        print(f"[-] Dedicated player post creation failed; falling back to slot pool: {e}")
                                        post_url = None
                                        slot = select_player_slot(match, schedule, player_slots, now, scheduler_config)
                                else:
                                    slot = select_player_slot(match, schedule, player_slots, now, scheduler_config)

                                if not post_url and not slot:
                                    print(f"[!] No free player slot available for {match['match_name']}.")
                                    # Only set preparing if neither live nor preparing state is already set
                                    if portal_updates_enabled and not match.get("new_blog_iframe_set") and not match.get("new_blog_prepare_set"):
                                        try:
                                            new_token = get_access_token(new_config)
                                            update_portal_match_page(automation_config, new_config, new_token, match, "preparing", schedule=schedule)
                                            match["new_blog_prepare_set"] = True
                                        except Exception as e:
                                            print(f"[-] Portal preparing-state update failed: {e}")
                                    match["status"] = "pending"
                                    changed = True
                                    continue

                                if not post_url and slot:
                                    post_title = slot.get("title") or (match["match_name"] + " Live Stream")
                                    post_id = slot["post_id"]
                                    print(f"[*] Updating player slot {slot['id']} ({post_id})...")
                                    post_url = update_blogger_post(config, token, post_id, post_title, player_html)
                                    print(f"[+] Player slot updated successfully! URL: {post_url}")
                                    match["player_slot_id"] = slot["id"]
                                    match["player_slot_post_id"] = post_id
                                    match["player_slot_url"] = post_url or slot.get("url", "")
                                    match["blogger_post_id"] = post_id
                                    match["blogger_post_url"] = post_url
                            match.pop("iframe_embed_code", None)
                        except Exception as e:
                            print(f"[-] Blogger upload failed: {e}")
                            match["status"] = "pending"
                            post_url = None
                    else:
                        print("[!] Blogger OAuth not fully configured for stream host blog.")

                    # Step 3: Generate portal buttons and update the NEW Blogger page when real links exist
                    links_written = False
                    if post_url and real_stream_links:
                        links_written = write_direct_links(match["match_name"], post_url, player_html, automation_config)

                    if portal_updates_enabled and post_url:
                        try:
                            print("[*] Fetching access token for the portal blog...")
                            new_token = get_access_token(new_config)
                            if real_stream_links and links_written:
                                links_html = read_links_html(match["match_name"], automation_config)
                                if not links_html:
                                    print("[!] Stream links were extracted but links HTML is missing; setting preparing state.")
                                    # Only set preparing if page hasn't already been set to live
                                    if not match.get("new_blog_iframe_set"):
                                        update_portal_match_page(automation_config, new_config, new_token, match, "preparing", schedule=schedule)
                                        match["new_blog_prepare_set"] = True
                                else:
                                    new_post_url = update_portal_match_page(automation_config, new_config, new_token, match, "live", links_html=links_html, schedule=schedule)
                                    print(f"[+] Portal page updated with live links: {new_post_url}")
                                    match["new_blog_iframe_set"] = True
                                    match["new_blog_prepare_set"] = False
                            elif real_stream_links:
                                print("[!] Stream links were extracted but no valid portal button HTML was written; setting preparing state.")
                                if not match.get("new_blog_iframe_set"):
                                    update_portal_match_page(automation_config, new_config, new_token, match, "preparing", schedule=schedule)
                                    match["new_blog_prepare_set"] = True
                            elif not match.get("new_blog_prepare_set") and not match.get("new_blog_iframe_set"):
                                # Only set preparing if the page hasn't already gone live
                                update_portal_match_page(automation_config, new_config, new_token, match, "preparing", schedule=schedule)
                                match["new_blog_prepare_set"] = True
                        except Exception as e:
                            print(f"[-] Portal blog update failed: {e}")

                    # Keep as pending while active so it can update again
                    # (last_run_time was already stamped before the scrape began)
                    match["status"] = "pending"
                    changed = True

            elif now > run_end:
                print(f"[*] Match active window ended: {match['match_name']}")
                portal_updates_enabled = has_new_oauth and not is_manual_portal_match(match)
                metadata_changed = False
                # Track whether the result just transitioned to "final" in this run
                was_final_before = result_is_final(match)
                try:
                    # Use score_only=True and respect cooldown intervals to avoid
                    # hammering the API every minute.  force=True was causing
                    # metadata_changed to always be True (timestamp update), which
                    # cascaded into redundant portal/post updates that exhausted
                    # the daily Blogger API quota.
                    metadata_changed = refresh_match_metadata(
                        match,
                        scheduler_config,
                        now,
                        active=False,
                        force=False,
                        score_only=True,
                    )
                    if metadata_changed:
                        print(f"[*] Final metadata checked/updated for: {match['match_name']}")
                except Exception as e:
                    print(f"[-] Final metadata refresh failed: {e}")

                newly_final = result_is_final(match) and not was_final_before

                # Only update portal page when first entering ended state, or when
                # the result just became final (to show the final score).  Previously
                # this fired on every metadata_changed which was always True.
                if portal_updates_enabled and (not match.get("new_blog_ended_set") or newly_final):
                    try:
                        new_token = get_access_token(new_config)
                        update_portal_match_page(automation_config, new_config, new_token, match, "ended", schedule=schedule)
                        match["new_blog_ended_set"] = True
                        match["new_blog_iframe_set"] = False
                        match["new_blog_prepare_set"] = False
                    except Exception as e:
                        print(f"[-] Portal ended-state update failed: {e}")

                post_id = str(match.get("new_blogger_post_id") or "").strip()
                # Only refresh the preview post when it hasn't been put down yet,
                # or when the result just transitioned to final (to embed the score).
                needs_post_update = not match.get("new_blog_post_put_down") or newly_final
                if portal_updates_enabled and post_id and needs_post_update:
                    try:
                        new_token = get_access_token(new_config)
                        render_match = with_thumbnail_src(automation_config, new_config, match)
                        post_html = render_preview_post(new_config, render_match)
                        try:
                            published_dt = "2000-01-01T00:00:00Z"
                        except Exception:
                            published_dt = None
                        post_url = update_blogger_post(
                            new_config,
                            new_token,
                            post_id,
                            preview_post_title(render_match, new_config),
                            post_html,
                            published=published_dt,
                            preserve_existing_thumbnail=True,
                        )
                        match["new_blogger_post_url"] = post_url
                        match["new_blog_post_put_down"] = True
                        record_content_hash(match, "preview_post", post_html)
                        print(f"[+] Preview post refreshed after match end (put down): {post_url}")
                    except Exception as e:
                        print(f"[-] Preview post ended-state refresh failed: {e}")

                # Put down the player dedicated post if one exists
                dedicated_post_id = dedicated_player_post_id(match, player_slots)
                if has_player_oauth and dedicated_post_id and not match.get("player_post_put_down"):
                    try:
                        token = get_access_token(config)
                        post_title = match["match_name"] + " Live Stream"
                        empty_html = render_player_html("const STREAM_LINKS = [];")
                        try:
                            published_dt = "2000-01-01T00:00:00Z"
                        except Exception:
                            published_dt = None
                        update_blogger_post(
                            config,
                            token,
                            dedicated_post_id,
                            post_title,
                            empty_html,
                            published=published_dt,
                        )
                        match["player_post_put_down"] = True
                        print(f"[+] Player dedicated post cleared and put down successfully: {dedicated_post_id}")
                    except Exception as e:
                        print(f"[-] Player dedicated post put down failed: {e}")

                if result_is_final(match) or completion_grace_expired(match, scheduler_config, now):
                    if not result_is_final(match):
                        checked_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                        existing_result = match.get("result") if isinstance(match.get("result"), dict) else {}
                        match["result"] = dict(existing_result, status="final_unverified", checked_at=checked_at, final_at=checked_at)
                        print(f"[!] Final score not verified for {match['match_name']} before grace deadline.")
                    else:
                        score = final_score_text(match)
                        if score:
                            print(f"[+] Final score verified for {match['match_name']}: {score}")
                    match["status"] = "completed"
                    # Clean up stale player HTML file for this match
                    try:
                        match_slug = slugify_match_name(match['match_name'])
                        player_file = os.path.join(paths["players_dir"], f"player_{match_slug}.html")
                        if os.path.exists(player_file):
                            os.remove(player_file)
                            print(f"[*] Cleaned up stale player file: {player_file}")
                        # Also clean up link files
                        links_file = os.path.join(paths.get("links_dir", os.path.join(paths["data_dir"], "links")), f"links_{match_slug}.html")
                        if os.path.exists(links_file):
                            os.remove(links_file)
                            print(f"[*] Cleaned up stale links file: {links_file}")
                        # Clean up crawl diagnostics
                        diag_dir = paths.get("diagnostics_dir", os.path.join(paths["data_dir"], "scraped_details"))
                        if os.path.isdir(diag_dir):
                            for diag_name in os.listdir(diag_dir):
                                if match_slug in diag_name:
                                    diag_path = os.path.join(diag_dir, diag_name)
                                    os.remove(diag_path)
                                    print(f"[*] Cleaned up diagnostic: {diag_path}")
                    except Exception as e:
                        print(f"[!] Warning: match file cleanup error: {e}")
                else:
                    match["status"] = "ended"
                    print(f"[*] Waiting for verified final score before archiving {match['match_name']}.")
                changed = True

        except Exception as e:
            import traceback
            print(f"[-] Unhandled error processing match {match.get('match_name')}: {e}")
            traceback.print_exc()

    # Clean up player slots that are no longer occupied by any active match
    occupied_slot_ids = set()
    for m in schedule:
        if m.get("status") not in ("completed", "ended"):
            slot_id = m.get("player_slot_id")
            if slot_id:
                occupied_slot_ids.add(slot_id)

    state = load_scheduler_state(automation_config)
    cleared_slots = state.get("cleared_player_slots") or []
    if not isinstance(cleared_slots, list):
        cleared_slots = []

    state_changed = False
    for slot in player_slots:
        slot_id = slot.get("id")
        post_id = slot.get("post_id")
        slot_title = slot.get("title") or "World Cup Live Player"
        if not slot_id or not post_id:
            continue

        if slot_id in occupied_slot_ids:
            if slot_id in cleared_slots:
                cleared_slots.remove(slot_id)
                state_changed = True
        else:
            if slot_id not in cleared_slots:
                print(f"[*] Clearing player slot {slot_id} ({post_id}) on Blogger...")
                try:
                    empty_html = render_player_html("const STREAM_LINKS = [];")
                    if has_player_oauth:
                        token = get_access_token(config)
                        update_blogger_post(config, token, post_id, slot_title, empty_html)
                        print(f"[+] Player slot {slot_id} cleared successfully.")
                        cleared_slots.append(slot_id)
                        state_changed = True
                    else:
                        print(f"[!] Cannot clear slot {slot_id}: Blogger OAuth not configured.")
                except Exception as e:
                    print(f"[-] Failed to clear player slot {slot_id}: {e}")

    if state_changed:
        state["cleared_player_slots"] = cleared_slots
        save_scheduler_state(state, automation_config)

    if changed:
        cleanup_completed_portal_pages(schedule, automation_config, new_config, now)
        schedule, archived_after = archive_completed_matches(schedule, automation_config, scheduler_config, now)
        if archived_after:
            print(f"[*] Archived {len(archived_after)} completed match(es) after scheduler run.")
        save_schedule(schedule, automation_config)

    # Run auto-discovery AFTER match processing to avoid blocking live match updates
    try:
        auto_discover_matches()
    except Exception as e:
        print(f"[-] Auto-discovery failed: {e}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Match Scheduler and Automator")
    parser.add_argument("--once", action="store_true", help="Run once and exit (for cronjobs)")
    args = parser.parse_args()
    automation_config = load_automation_config()
    paths = ensure_runtime_dirs(automation_config)

    def locked_check():
        with open(paths["lock_file"], "w", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("[*] Scheduler already running; skipping overlapping invocation.")
                return
            lock_file.write(str(os.getpid()))
            lock_file.truncate()
            check_and_run()

    if args.once:
        print("[*] Running scheduler in one-off mode...")
        locked_check()
    else:
        scheduler_config = get_scheduler_config(automation_config)
        loop_interval = int(scheduler_config.get("loop_interval_seconds", 60))
        print(f"[*] Match Scheduler started. Checking every {loop_interval} seconds...")
        while True:
            try:
                locked_check()
            except Exception as e:
                print(f"[-] Scheduler iteration failed: {e}")
            time.sleep(loop_interval)

if __name__ == "__main__":
    main()
