#!/usr/bin/env python3
import os
import sys
import json
import time
import subprocess
from datetime import datetime, timezone, timedelta
import requests
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
    archive_completed_matches,
    ensure_runtime_dirs,
    load_schedule,
    match_key,
    save_schedule,
    storage_config,
)
from portal_renderer import (
    parse_match_time,
    preview_post_title,
    render_preview_post,
    render_streaming_page,
    slugify_match_name,
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
            direct_url = f"{post_url}?link={idx + 1}"

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


def update_portal_match_page(automation_config, new_config, new_token, match, state, links_html=""):
    if is_manual_portal_match(match):
        print(f"[*] Skipping manual portal update for {match['match_name']} as state={state}.")
        return match.get("new_blogger_page_url") or match.get("new_blogger_post_url")

    render_match = with_thumbnail_src(automation_config, new_config, match)
    title = streaming_page_title(render_match, new_config)
    page_html = render_streaming_page(new_config, render_match, state=state, links_html=links_html)
    page_id = str(match.get("new_blogger_page_id") or "").strip()
    if page_id and not page_id.startswith("YOUR_"):
        print(f"[*] Updating portal Page {page_id} as state={state}...")
        page_url = update_blogger_page(new_config, new_token, page_id, title, page_html)
    else:
        print("[*] Portal Page ID missing; searching or creating the canonical streaming Page...")
        found_id, found_url = find_existing_blogger_page(new_config, new_token, title, match["match_name"])
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

last_discovery_time = 0

MATCH_ALIASES = {
    "qat": "qatar",
    "qater": "qatar",
    "switz": "switzerland",
    "switzrlnd": "switzerland",
    "swi": "switzerland",
    "scot": "scotland",
    "scotlnd": "scotland",
    "sco": "scotland",
    "tur": "turkiye",
    "turk": "turkiye",
    "turkey": "turkiye",
    "aus": "australia",
    "austrliaturky": "australia turkiye",
    "bra": "brazil",
    "mor": "morocco",
    "moroco": "morocco",
    "para": "paraguay",
    "par": "paraguay",
    "canad": "canada",
    "bosniahrg": "bosnia",
    "safrica": "south africa",
    "korea": "korea",
    "czech": "czechia",
    "czechia": "czechia"
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
    return len(match_tokens & candidate_tokens)


def candidate_text_for_url(url, anchor_text=""):
    parsed = urlparse(url)
    path_text = parsed.path.replace("/", " ").replace("-", " ").replace("_", " ")
    return f"{anchor_text or ''} {path_text}"


def source_matches_schedule_item(match, candidate_text):
    score = match_source_score(match.get("match_name", ""), candidate_text)
    match_token_count = len(normalize_match_tokens(match.get("match_name", "")))
    if match_token_count <= 2:
        return score >= match_token_count
    return score >= 2


def source_urls_for_match(match):
    value = match.get("source_url", "")
    if isinstance(value, list):
        return [u for u in value if u]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


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
    link_pattern = re.compile(r'<a\b([^>]*)>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    for match in link_pattern.finditer(html or ""):
        attrs = match.group(1)
        body = re.sub(r"<[^>]+>", " ", match.group(2))
        body = re.sub(r"\s+", " ", body).strip()
        href_match = re.search(r'href=[\x27"]([^\x27"]+)[\x27"]', attrs, re.IGNORECASE)
        if not href_match:
            continue
        resolved_url = urljoin(portal, href_match.group(1))
        u_lower = resolved_url.lower()
        if any(part in u_lower for part in STATIC_LINK_PARTS):
            continue
        if not trusted_domain(resolved_url, trusted_domains):
            continue
        parsed = urlparse(resolved_url)
        if not parsed.scheme.startswith("http"):
            continue
        if not parsed.path or parsed.path == "/":
            continue
        canon = canonical_url(resolved_url)
        if canon in seen:
            continue
        seen.add(canon)
        candidates.append({
            "url": resolved_url,
            "text": candidate_text_for_url(resolved_url, body)
        })

    for raw_url in re.findall(r'https?://[^\s\x27"<>]+', html or ""):
        resolved_url = raw_url.rstrip("),.;")
        u_lower = resolved_url.lower()
        if any(part in u_lower for part in STATIC_LINK_PARTS):
            continue
        if not trusted_domain(resolved_url, trusted_domains):
            continue
        canon = canonical_url(resolved_url)
        if canon in seen:
            continue
        seen.add(canon)
        candidates.append({
            "url": resolved_url,
            "text": candidate_text_for_url(resolved_url)
        })
    return candidates


def extract_match_name_from_candidate(candidate_text):
    from generate_player import extract_match_name
    match_name = extract_match_name(candidate_text)
    if match_name:
        return match_name.title()
    return None


def auto_discover_matches(force=False):
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
            r = requests.get(portal, headers=headers, timeout=12)
            if r.status_code != 200:
                continue

            for candidate in extract_discovery_candidates(portal, r.text, trusted_domains):
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
                    if source_matches_schedule_item(existing_match, candidate_text):
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

    # Run auto-discovery first
    try:
        auto_discover_matches()
    except Exception as e:
        print(f"[-] Auto-discovery failed: {e}")

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
    schedule, archived = archive_completed_matches(schedule, automation_config, scheduler_config, now)
    if archived:
        print(f"[*] Archived {len(archived)} completed match(es) before scheduler run.")
        changed = True

    for match in schedule:
        status = match.get("status", "pending")
        if status == "completed":
            continue
        # Self-heal: if a previous run crashed mid-way and left status=processing,
        # treat it as pending so the scheduler retries instead of freezing.
        if status == "processing":
            print(f"[!] Match '{match.get('match_name')}' found in 'processing' state — resetting to 'pending' for retry.")
            match["status"] = "pending"
            status = "pending"
        match["match_key"] = match.get("match_key") or match_key(match)
        portal_updates_enabled = has_new_oauth and not is_manual_portal_match(match)

        match_time = parse_time(match["match_time"])
        run_start, run_end, _ = active_window(match, scheduler_config)

        if run_start <= now <= run_end:
            cooldown_min = scrape_cooldown_minutes(now, match_time, scheduler_config)

            # Failure backoff: if we have had 3+ consecutive failures, skip until
            # at least (fail_count * cooldown_min) minutes have passed since last run.
            fail_count = int(match.get("scrape_fail_count") or 0)
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
                
                # Step 1: Run generate_player.py to crawl and produce player file
                sources = source_urls_for_match(match)
                if not sources:
                    print(f"[!] No source URLs available yet for {match['match_name']}; waiting for source discovery.")
                    if portal_updates_enabled and not match.get("new_blog_prepare_set") and not match.get("new_blog_iframe_set"):
                        try:
                            new_token = get_access_token(new_config)
                            update_portal_match_page(automation_config, new_config, new_token, match, "preparing")
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
                    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    print(f"[+] Scraping successful. Generated {temp_output}")
                    match["scrape_fail_count"] = 0  # Reset failure counter on success
                    mark_source_success(match, sources)
                except Exception as e:
                    print(f"[-] Scraping failed: {e}")
                    # Increment failure counter for backoff logic
                    match["scrape_fail_count"] = int(match.get("scrape_fail_count") or 0) + 1
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
                    match["scrape_fail_count"] = int(match.get("scrape_fail_count") or 0) + 1
                    match["status"] = "pending"
                    changed = True
                    continue

                real_stream_links = extract_stream_links(player_html)
                if not real_stream_links:
                    print(f"[!] No playable stream links resolved for {match['match_name']}; skipping player-blog upload.")
                    # Only set preparing state if the portal page hasn't been set to live yet
                    # (avoid overwriting live links with "preparing" message)
                    if portal_updates_enabled and not match.get("new_blog_prepare_set") and not match.get("new_blog_iframe_set"):
                        try:
                            new_token = get_access_token(new_config)
                            update_portal_match_page(automation_config, new_config, new_token, match, "preparing")
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
                                        update_portal_match_page(automation_config, new_config, new_token, match, "preparing")
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
                                    update_portal_match_page(automation_config, new_config, new_token, match, "preparing")
                                    match["new_blog_prepare_set"] = True
                            else:
                                new_post_url = update_portal_match_page(automation_config, new_config, new_token, match, "live", links_html=links_html)
                                print(f"[+] Portal page updated with live links: {new_post_url}")
                                match["new_blog_iframe_set"] = True
                                match["new_blog_prepare_set"] = False
                        elif real_stream_links:
                            print("[!] Stream links were extracted but no valid portal button HTML was written; setting preparing state.")
                            if not match.get("new_blog_iframe_set"):
                                update_portal_match_page(automation_config, new_config, new_token, match, "preparing")
                                match["new_blog_prepare_set"] = True
                        elif not match.get("new_blog_prepare_set") and not match.get("new_blog_iframe_set"):
                            # Only set preparing if the page hasn't already gone live
                            update_portal_match_page(automation_config, new_config, new_token, match, "preparing")
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
            try:
                metadata_changed = refresh_match_metadata(
                    match,
                    scheduler_config,
                    now,
                    active=True,
                    force=True,
                    score_only=False,
                )
                if metadata_changed:
                    print(f"[*] Final metadata checked/updated for: {match['match_name']}")
            except Exception as e:
                print(f"[-] Final metadata refresh failed: {e}")

            if portal_updates_enabled and (metadata_changed or not match.get("new_blog_ended_set")):
                try:
                    new_token = get_access_token(new_config)
                    update_portal_match_page(automation_config, new_config, new_token, match, "ended")
                    match["new_blog_ended_set"] = True
                    match["new_blog_iframe_set"] = False
                    match["new_blog_prepare_set"] = False
                except Exception as e:
                    print(f"[-] Portal ended-state update failed: {e}")

            post_id = str(match.get("new_blogger_post_id") or "").strip()
            if portal_updates_enabled and post_id and (metadata_changed or result_is_final(match) or not match.get("new_blog_post_put_down")):
                try:
                    new_token = get_access_token(new_config)
                    render_match = with_thumbnail_src(automation_config, new_config, match)
                    post_html = render_preview_post(new_config, render_match)
                    try:
                        kickoff = parse_match_time(match["match_time"])
                        published_dt = (kickoff - timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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
                        kickoff = parse_match_time(match["match_time"])
                        published_dt = (kickoff - timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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
                    player_file = os.path.join(paths["players_dir"], f"player_{slugify_match_name(match['match_name'])}.html")
                    if os.path.exists(player_file):
                        os.remove(player_file)
                        print(f"[*] Cleaned up stale player file: {player_file}")
                except Exception as e:
                    print(f"[!] Warning: could not remove player file: {e}")
            else:
                match["status"] = "ended"
                print(f"[*] Waiting for verified final score before archiving {match['match_name']}.")
            changed = True

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
        schedule, archived_after = archive_completed_matches(schedule, automation_config, scheduler_config, now)
        if archived_after:
            print(f"[*] Archived {len(archived_after)} completed match(es) after scheduler run.")
        save_schedule(schedule, automation_config)

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
