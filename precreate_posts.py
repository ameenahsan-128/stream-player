#!/usr/bin/env python3
import os
import sys
import json
import time
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

import math
from datetime import datetime, timedelta, timezone
import re

# Global rate-limit state: once a 429 "daily quota" error is hit, skip all
# further API calls for this process lifetime to avoid burning quota.
_rate_limit_until = 0  # epoch timestamp; skip API calls while time.time() < this
_DAILY_QUOTA_BACKOFF_SECONDS = 300  # 5 min backoff on daily quota exhaustion
_PER_MINUTE_BACKOFF_SECONDS = 30    # 30s backoff on per-minute rate limit


class BloggerRateLimitError(Exception):
    """Raised when the Blogger API returns 429 Too Many Requests."""
    def __init__(self, message, is_daily=False):
        super().__init__(message)
        self.is_daily = is_daily


def _check_rate_limit():
    """Raise if we are in a rate-limit cooldown period."""
    if time.time() < _rate_limit_until:
        remaining = int(_rate_limit_until - time.time())
        raise BloggerRateLimitError(
            f"Blogger API rate-limited; skipping call ({remaining}s remaining in cooldown)",
            is_daily=True,
        )


def _handle_response(response):
    """Check for 429 and set global backoff before raising."""
    global _rate_limit_until
    if response.status_code == 429:
        body = response.text or ""
        is_daily = "per day" in body.lower()
        backoff = _DAILY_QUOTA_BACKOFF_SECONDS if is_daily else _PER_MINUTE_BACKOFF_SECONDS
        _rate_limit_until = time.time() + backoff
        kind = "daily quota" if is_daily else "per-minute rate limit"
        print(f"[!] Blogger API 429: {kind} exceeded — backing off {backoff}s")
        raise BloggerRateLimitError(
            f"429 Too Many Requests ({kind}): {response.text[:200]}",
            is_daily=is_daily,
        )
    response.raise_for_status()


from automation_config import get_fixture_api_config, get_portal_blog_config, get_scheduler_config, has_oauth, load_automation_config
from fixture_manager import schedule_match_allowed
from lineup_manager import refresh_lineups_for_match
from match_metadata import content_hash, refresh_match_metadata
from pipeline_storage import archive_completed_matches, ensure_runtime_dirs, load_schedule, match_key, save_schedule
from portal_renderer import (
    IST,
    get_channel_info,
    get_team_title_code,
    display_team_name,
    normalize_team_key,
    parse_match_time,
    preview_post_title,
    render_preview_post,
    render_streaming_page,
    slugify_match_name,
    split_teams,
    streaming_page_title,
    streaming_page_url_seed_title,
)
from thumbnail_manager import refresh_thumbnail_url_from_sources, sanitize_thumbnail_fields, thumbnail_path, with_thumbnail_src

CONFIG_FILE = "new_blogger_config.json"

# Exact ad code scripts and popup wrappers matching goforsports.net
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
  <div id="popup-ad-inner" style="background: rgb(255, 255, 255); border-radius: 8px; padding: 10px; position: relative; min-width: 300px; min-height: 250px;">
    <button onclick="document.getElementById('popup-ad-overlay').style.display='none'" style="background: rgb(51, 51, 51); border: none; color: white; cursor: pointer; font-size: 16px; height: 26px; line-height: 1; position: absolute; right: -12px; top: -12px; width: 26px; border-radius: 50%; z-index: 100000;">&times;</button>
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
      var overlay = document.getElementById('popup-ad-overlay');
      var inner = document.getElementById('popup-ad-inner');
      if (!overlay || !inner) return;
      var adFrame = inner.querySelector('iframe');
      if (adFrame) {
        overlay.style.display = 'flex';
      } else {
        var retries = 0;
        var checkAd = setInterval(function() {
          retries++;
          var frame = inner.querySelector('iframe');
          if (frame) {
            clearInterval(checkAd);
            overlay.style.display = 'flex';
          } else if (retries >= 10) {
            clearInterval(checkAd);
          }
        }, 500);
      }
    }, 3000); // Trigger popup after 3 seconds
  });
</script>
<!-- Adsterra Integration Scripts -->
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

def create_blogger_post(config, access_token, title, html_content, published=None):
    _check_rate_limit()
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
    _handle_response(response)
    res_data = response.json()
    return res_data.get("id"), res_data.get("url")


def html_has_image(html_content):
    return bool(re.search(r"<img\b", html_content or "", flags=re.IGNORECASE))


def extract_first_image_html(html_content):
    match = re.search(r"<img\b[^>]*>", html_content or "", flags=re.IGNORECASE)
    return match.group(0) if match else ""


def preserve_existing_post_thumbnail(config, access_token, post_id, html_content):
    if html_has_image(html_content):
        return html_content

    _check_rate_limit()
    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/{post_id}"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(url, headers=headers, params={"fields": "content"}, timeout=20)
        _handle_response(response)
    except BloggerRateLimitError:
        raise  # Let the caller see the rate limit so it can skip further calls
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
    _check_rate_limit()
    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/{post_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    if preserve_existing_thumbnail:
        html_content = preserve_existing_post_thumbnail(config, access_token, post_id, html_content)
    payload = {
        "kind": "blogger#post",
        "id": post_id,
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    if published:
        payload["published"] = published
    response = requests.patch(url, headers=headers, json=payload, timeout=20)
    _handle_response(response)
    return response.json().get("url")

def create_blogger_page(config, access_token, title, html_content):
    _check_rate_limit()
    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "kind": "blogger#page",
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    response = requests.post(url, headers=headers, json=payload, timeout=20)
    _handle_response(response)
    res_data = response.json()
    return res_data.get("id"), res_data.get("url")


def create_stream_blogger_page(config, access_token, match, title, html_content):
    seed_title = streaming_page_url_seed_title(match, config)
    if normalize_title(seed_title) == normalize_title(title):
        return create_blogger_page(config, access_token, title, html_content)

    page_id, page_url = create_blogger_page(config, access_token, seed_title, html_content)
    patched_url = update_blogger_page(config, access_token, page_id, title, html_content)
    return page_id, patched_url or page_url

def update_blogger_page(config, access_token, page_id, title, html_content):
    _check_rate_limit()
    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "kind": "blogger#page",
        "id": page_id,
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    response = requests.patch(url, headers=headers, json=payload, timeout=20)
    _handle_response(response)
    return response.json().get("url")


def normalize_title(title):
    return re.sub(r"\s+", " ", (title or "").strip()).lower()


MATCH_ALIASES = {
    "am": "australia",
    "qat": "qatar",
    "qater": "qatar",
    "cura": "curacao",
    "curacao": "curacao",
    "switz": "switzerland",
    "swi": "switzerland",
    "scot": "scotland",
    "sco": "scotland",
    "turk": "turkiye",
    "turkey": "turkiye",
    "aus": "australia",
    "bra": "brazil",
    "mor": "morocco",
    "para": "paraguay",
    "par": "paraguay"
}

MATCH_STOP_WORDS = {
    "fifa", "world", "cup", "2026", "match", "preview", "live", "info",
    "stream", "streaming", "portal", "watch", "and", "the", "vs", "v",
    "team"
}


def match_words(text):
    words = []
    for raw in re.findall(r"[a-z0-9]+", (text or "").lower()):
        word = MATCH_ALIASES.get(raw, raw)
        if word not in MATCH_STOP_WORDS and (len(word) > 2 or word == "usa"):
            words.append(word)
    return set(words)


def item_matches_title_or_match(item, title, match_name=None, allow_title_only=False):
    item_title = item.get("title", "")
    expected = match_words(match_name or "")
    combined = match_words(f"{item_title} {item.get('url', '')}")
    if normalize_title(item_title) == normalize_title(title):
        if allow_title_only or not expected:
            return True
        return expected.issubset(combined)
    if not match_name:
        return False
    return len(expected) >= 2 and expected.issubset(combined)


def item_matches_team_page(item, team, config=None):
    team = display_team_name(team)
    if not team:
        return False

    title = item.get("title", "")
    url = item.get("url", "")
    text_tokens = set(re.findall(r"[a-z0-9]+", f"{title} {url}".lower()))
    team_words = match_words(team)
    if team_words and team_words.issubset(match_words(f"{title} {url}")):
        return True

    team_code = get_team_title_code(config or {}, team)
    candidate_tokens = {
        normalize_team_key(team),
        normalize_team_key(display_team_name(team)),
        normalize_team_key(team_code),
    }
    for candidate in candidate_tokens:
        candidate_parts = candidate.split()
        if candidate_parts and all(part in text_tokens for part in candidate_parts):
            return True

    return False


def team_page_reuse_score(item, team, config=None):
    title = item.get("title", "")
    url = (item.get("url") or "").lower()
    team = display_team_name(team)
    team_slug = re.sub(r"[^a-z0-9]+", "-", normalize_team_key(team)).strip("-")
    team_code = normalize_team_key(get_team_title_code(config or {}, team)).upper()
    score = 100

    if team_slug and url.endswith(f"/{team_slug}-info.html"):
        score = 0
    elif team_code and normalize_title(title) == normalize_title(f"{team_code} INFO"):
        score = 5
    elif team_slug and f"/{team_slug}" in url and "info" in url:
        score = 10
    elif "info" in url:
        score = 20
    elif "live-streaming" in url:
        score = 30
    if "-vs-" in url:
        score += 10
    return score


def find_existing_blogger_post(config, access_token, title, match_name=None):
    blog_id = config.get("blog_id")
    headers = {"Authorization": f"Bearer {access_token}"}

    search_url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/search"
    try:
        response = requests.get(
            search_url,
            headers=headers,
            params={"q": title, "fetchBodies": "false"},
            timeout=20
        )
        response.raise_for_status()
        for item in response.json().get("items", []):
            if item_matches_title_or_match(item, title, match_name):
                return item.get("id"), item.get("url")
    except Exception as e:
        print(f"[!] Post search failed, falling back to recent post list: {e}")

    list_url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts"
    page_token = None
    for _ in range(5):
        params = {"fetchBodies": "false", "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(list_url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        for item in data.get("items", []):
            if item_matches_title_or_match(item, title, match_name, allow_title_only=True):
                return item.get("id"), item.get("url")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return None, None


def find_existing_blogger_page(config, access_token, title, match_name=None):
    blog_id = config.get("blog_id")
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages"
    page_token = None
    matches = []
    team_matches = []

    def page_reuse_score(item):
        item_url = (item.get("url") or "").lower()
        title_slug = re.sub(r"[^a-z0-9]+", "-", normalize_title(title)).strip("-")
        score = 100
        if title_slug and item_url.endswith(f"/{title_slug}.html"):
            score = 0
        elif item_url.endswith("-info.html") and "-vs-" not in item_url and "live-streaming" not in item_url:
            score = 10
        elif "live-streaming" not in item_url:
            score = 20
        else:
            score = 30
        if normalize_title(item.get("title", "")) == normalize_title(title):
            score -= 5
        return score

    for _ in range(5):
        params = {"fetchBodies": "false", "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        for item in data.get("items", []):
            if item_matches_title_or_match(item, title, match_name, allow_title_only=True):
                matches.append(item)
            elif match_name:
                team1, team2 = split_teams(match_name)
                for priority, team in enumerate((team1, team2)):
                    if item_matches_team_page(item, team, config):
                        team_matches.append((priority, team, item))
                        break
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    if matches:
        best = sorted(matches, key=page_reuse_score)[0]
        return best.get("id"), best.get("url")
    if team_matches:
        priority, team, best = sorted(
            team_matches,
            key=lambda row: (row[0], team_page_reuse_score(row[2], row[1], config))
        )[0]
        print(f"[*] Reusing existing team Page for {match_name}: {display_team_name(team)} | {best.get('url')}")
        return best.get("id"), best.get("url")
    return None, None

def generate_thumbnail(team1, team2, match_time_str, output_path, league="", channel=""):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[!] Pillow library not found. Skipping image generation.")
        return False

    output_size = (1280, 720)
    img = Image.new("RGB", output_size, color="#fbfbf7")
    draw = ImageDraw.Draw(img)
    width, height = img.size

    font_paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )

    def load_font(size):
        for path in font_paths:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def text_size(text, font):
        try:
            box = draw.textbbox((0, 0), text, font=font)
            return box[2] - box[0], box[3] - box[1]
        except Exception:
            return draw.textlength(text, font=font), font.size if hasattr(font, "size") else 20

    def centered_text(text, y, font, fill, stroke_width=0, stroke_fill="#ffffff"):
        text = str(text or "")
        tw, _ = text_size(text, font)
        draw.text(((width - tw) / 2, y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)

    def fit_font(text, max_width, start_size, min_size=28):
        size = start_size
        while size > min_size:
            font = load_font(size)
            tw, _ = text_size(text, font)
            if tw <= max_width:
                return font
            size -= 3
        return load_font(min_size)

    def team_colors(name):
        palettes = {
            "qatar": ["#8a1538", "#ffffff"],
            "switzerland": ["#e30613", "#ffffff"],
            "scotland": ["#005eb8", "#ffffff"],
            "haiti": ["#00209f", "#d21034"],
            "turkey": ["#e30a17", "#ffffff"],
            "turkiye": ["#e30a17", "#ffffff"],
            "australia": ["#012169", "#ffcd00", "#00843d"],
            "brazil": ["#009b3a", "#ffdf00", "#002776"],
            "morocco": ["#c1272d", "#006233"],
            "mexico": ["#006847", "#ffffff", "#ce1126"],
            "south africa": ["#007a4d", "#ffb612", "#de3831", "#002395"],
        }
        key = re.sub(r"[^a-z0-9 ]+", "", str(name or "").lower()).strip()
        if key in palettes:
            return palettes[key]
        seed = sum(ord(char) for char in key)
        base = ["#004c97", "#0b8f3a", "#c8102e", "#ffb300", "#6a1b9a", "#111111"]
        return [base[seed % len(base)], base[(seed + 2) % len(base)]]

    def draw_star(cx, cy, radius, fill):
        points = []
        for idx in range(10):
            angle = -90 + idx * 36
            r = radius if idx % 2 == 0 else radius * 0.42
            points.append((cx + r * math.cos(math.radians(angle)), cy + r * math.sin(math.radians(angle))))
        draw.polygon(points, fill=fill)

    def draw_flag_content(x, y, w, h, name):
        key = re.sub(r"[^a-z0-9 ]+", "", str(name or "").lower()).strip()
        if key == "brazil":
            draw.rectangle([x, y, x + w, y + h], fill="#009b3a")
            draw.polygon([(x + w * .5, y + h * .12), (x + w * .88, y + h * .5), (x + w * .5, y + h * .88), (x + w * .12, y + h * .5)], fill="#ffdf00")
            draw.ellipse([x + w * .36, y + h * .28, x + w * .64, y + h * .56], fill="#002776")
            return
        if key == "morocco":
            draw.rectangle([x, y, x + w, y + h], fill="#c1272d")
            draw_star(x + w / 2, y + h / 2, min(w, h) * .19, "#006233")
            return
        if key in ("switzerland", "swiss"):
            draw.rectangle([x, y, x + w, y + h], fill="#e30613")
            draw.rectangle([x + w * .43, y + h * .24, x + w * .57, y + h * .76], fill="#ffffff")
            draw.rectangle([x + w * .26, y + h * .43, x + w * .74, y + h * .57], fill="#ffffff")
            return
        if key == "qatar":
            draw.rectangle([x, y, x + w, y + h], fill="#8a1538")
            tooth_w = w * .28
            draw.rectangle([x, y, x + tooth_w * .55, y + h], fill="#ffffff")
            points = []
            step = h / 9
            for i in range(10):
                points.append((x + tooth_w * (.55 if i % 2 == 0 else 1.0), y + i * step))
            points.extend([(x, y + h), (x, y)])
            draw.polygon(points, fill="#ffffff")
            return
        if key == "scotland":
            draw.rectangle([x, y, x + w, y + h], fill="#005eb8")
            draw.line([x, y, x + w, y + h], fill="#ffffff", width=max(8, int(h * .12)))
            draw.line([x + w, y, x, y + h], fill="#ffffff", width=max(8, int(h * .12)))
            return
        if key == "haiti":
            draw.rectangle([x, y, x + w, y + h / 2], fill="#00209f")
            draw.rectangle([x, y + h / 2, x + w, y + h], fill="#d21034")
            draw.rectangle([x + w * .38, y + h * .34, x + w * .62, y + h * .66], fill="#ffffff")
            return
        if key in ("turkey", "turkiye"):
            draw.rectangle([x, y, x + w, y + h], fill="#e30a17")
            draw.ellipse([x + w * .27, y + h * .28, x + w * .55, y + h * .72], fill="#ffffff")
            draw.ellipse([x + w * .34, y + h * .31, x + w * .60, y + h * .69], fill="#e30a17")
            draw_star(x + w * .66, y + h * .5, min(w, h) * .14, "#ffffff")
            return
        colors = team_colors(name)
        draw.rounded_rectangle([x, y, x + w, y + h], radius=24, fill="#ffffff", outline="#d6d6d6", width=3)
        stripe_w = max(1, w // len(colors))
        for idx, color in enumerate(colors):
            x1 = x + idx * stripe_w
            x2 = x + w if idx == len(colors) - 1 else x + (idx + 1) * stripe_w
            draw.rectangle([x1 + 8, y + 8, x2 - 8, y + h - 54], fill=color)

    def draw_flag_card(x, y, w, h, name):
        draw.rounded_rectangle([x, y, x + w, y + h], radius=24, fill="#ffffff", outline="#d6d6d6", width=3)
        draw_flag_content(x + 8, y + 8, w - 16, h - 60, name)
        label_font = fit_font(str(name).upper(), w - 28, 34, 22)
        tw, _ = text_size(str(name).upper(), label_font)
        draw.text((x + (w - tw) / 2, y + h - 45), str(name).upper(), font=label_font, fill="#071a33")

    try:
        if match_time_str.endswith("Z"):
            match_time_str = match_time_str[:-1] + "+00:00"
        match_dt = datetime.fromisoformat(match_time_str).astimezone(IST)
        time_text = match_dt.strftime("%d %b %Y | %I:%M %p IST").lstrip("0")
    except Exception:
        time_text = match_time_str

    team1 = display_team_name(team1 or "Team A")
    team2 = display_team_name(team2 or "Team B")
    genre = league if league and league != "TBA" else "FIFA World Cup 2026"
    template_path = "post_thumbnail_reference.jpg"
    if os.path.exists(template_path):
        template = Image.open(template_path).convert("RGB")
        ratio = max(width / template.width, height / template.height)
        resized = template.resize((int(template.width * ratio), int(template.height * ratio)), Image.Resampling.LANCZOS)
        left = (resized.width - width) // 2
        top = (resized.height - height) // 2
        img = resized.crop((left, top, left + width, top + height))
        draw = ImageDraw.Draw(img)

        draw.rounded_rectangle([230, 246, 528, 462], radius=26, fill="#ffffff", outline="#d7d7d7", width=4)
        draw.rounded_rectangle([672, 246, 970, 462], radius=26, fill="#ffffff", outline="#d7d7d7", width=4)
        draw.rounded_rectangle([530, 242, 670, 500], radius=18, fill="#fffefa", outline="#fffefa", width=1)
        draw.rectangle([292, 460, 520, 536], fill="#fffefa")
        draw.rectangle([690, 460, 930, 536], fill="#fffefa")
        draw_flag_card(242, 256, 274, 200, team1)
        draw_flag_card(684, 256, 274, 200, team2)
        centered_text("VS", 298, load_font(82), "#071a33")
    else:
        for offset, color in ((0, "#00843d"), (18, "#ffffff"), (36, "#d50032")):
            draw.arc([-180 + offset, -190 + offset, 520 + offset, 510 + offset], 85, 184, fill=color, width=18)
        for offset, color in ((0, "#d50032"), (18, "#ffffff"), (36, "#0057b8")):
            draw.arc([760 - offset, -120 + offset, 1460 - offset, 580 + offset], 350, 88, fill=color, width=18)
        draw.polygon([(0, 625), (150, 720), (0, 720)], fill="#004c97")
        draw.polygon([(1280, 610), (1125, 720), (1280, 720)], fill="#d50032")

        for x in range(0, width, 92):
            draw.line([(x, 520), (x - 190, 720)], fill="#e8edf2", width=2)
        draw.rectangle([0, 570, width, height], fill="#f5f7f8")
        draw.arc([90, 410, 1190, 1040], 200, 340, fill="#d9e2e7", width=3)
        draw.line([(125, 610), (1155, 610)], fill="#d9e2e7", width=3)

        centered_text("FIFA WORLD CUP", 42, load_font(44), "#071a33")
        centered_text("2026", 88, load_font(64), "#009b3a", stroke_width=1, stroke_fill="#ffffff")
        draw.ellipse([118, 70, 182, 134], outline="#d9c79e", width=8)
        draw.rectangle([136, 128, 164, 210], fill="#d9c79e")
        draw.pieslice([105, 185, 195, 260], 0, 180, fill="#d9c79e")
        draw_flag_card(160, 235, 330, 205, team1)
        draw_flag_card(790, 235, 330, 205, team2)
        centered_text("VS", 285, load_font(74), "#071a33")

        pill_x1, pill_y1, pill_x2, pill_y2 = 360, 472, 920, 536
        draw.rounded_rectangle([pill_x1, pill_y1, pill_x2, pill_y2], radius=34, fill="#ffffff", outline="#e0e0e0", width=3)
        draw.ellipse([pill_x1 + 22, pill_y1 + 16, pill_x1 + 46, pill_y1 + 40], fill="#4285f4")
        draw.text((pill_x1 + 72, pill_y1 + 15), "goforsports.net", font=load_font(30), fill="#1d1d1d")
        draw.ellipse([pill_x2 - 58, pill_y1 + 17, pill_x2 - 34, pill_y1 + 41], outline="#1d1d1d", width=4)
        draw.line([(pill_x2 - 38, pill_y1 + 38), (pill_x2 - 24, pill_y1 + 52)], fill="#1d1d1d", width=4)
        centered_text(str(genre).upper(), 555, fit_font(str(genre).upper(), 900, 34, 24), "#a67c2d")
        centered_text(time_text.upper(), 604, fit_font(time_text.upper(), 900, 30, 22), "#071a33")
        centered_text("USA | CANADA | MEXICO", 650, load_font(24), "#0057b8")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "JPEG", quality=90, optimize=True)
    print(f"[+] Generated thumbnail image: {output_path}")
    return True


def thumbnail_needs_generation(path):
    if not os.path.exists(path):
        return True
    try:
        from PIL import Image
        with Image.open(path) as image:
            ratio = image.width / float(image.height)
            return abs(ratio - (16 / 9)) > 0.01
    except Exception:
        return True

def generate_post_html(config, match, safe_name):
    match_name = match["match_name"]
    match_time_str = match["match_time"]
    
    name_parts = re.split(r'(?:Vs|vs|VS|\bv\b)', match_name)
    team1 = name_parts[0].strip() if len(name_parts) > 0 else "Team A"
    team2 = name_parts[1].strip() if len(name_parts) > 1 else "Team B"
    
    # Redirect button points to the Page URL
    page_url = match.get("new_blogger_page_url", "#")
    
    # Custom image URL
    image_base = config.get("image_base_url", "https://yourdomain.com/images/")
    thumbnail_url = f"{image_base}thumb_{safe_name}.jpg"
    
    # Ad layouts from configuration or default values
    ad_top = config.get("ad_code_top") or DEFAULT_AD_TOP
    ad_bottom = config.get("ad_code_bottom") or DEFAULT_AD_POPUP

    # Randomized WhatsApp and Telegram group redirect buttons
    whatsapp_groups = config.get("whatsapp_groups", [])
    telegram_channels = config.get("telegram_channels", [])
    
    wa_json = json.dumps(whatsapp_groups)
    tg_json = json.dumps(telegram_channels)
    
    social_buttons_html = ""
    social_script = ""
    
    if whatsapp_groups or telegram_channels:
        social_buttons_html += '<div style="text-align: center; margin: 15px 0; display: flex; justify-content: center; gap: 12px; flex-wrap: wrap;">'
        if whatsapp_groups:
            social_buttons_html += '<button onclick="openRandomWaGroup()" style="display: inline-block; background-color: #25d366; color: white; border: none; cursor: pointer; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">Join WhatsApp Group</button>'
        if telegram_channels:
            social_buttons_html += '<button onclick="openRandomTgGroup()" style="display: inline-block; background-color: #0088cc; color: white; border: none; cursor: pointer; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">Join Telegram Channel</button>'
        social_buttons_html += '</div>'
        
        social_script = f"""
<script type="text/javascript">
(function() {{
  const waPool = {wa_json};
  const tgPool = {tg_json};
  
  window.openRandomWaGroup = function() {{
    if (!waPool || waPool.length === 0) return;
    const idx = Math.floor(Math.random() * waPool.length);
    window.open(waPool[idx], '_blank');
  }};
  
  window.openRandomTgGroup = function() {{
    if (!tgPool || tgPool.length === 0) return;
    const idx = Math.floor(Math.random() * tgPool.length);
    window.open(tgPool[idx], '_blank');
  }};
}})();
</script>
"""

    # Optional random button
    random_button_html = ""
    random_btn_text = config.get("random_btn_text")
    random_btn_url = config.get("random_btn_url")
    if random_btn_text and random_btn_url:
        random_button_html = f'<div style="text-align: center; margin: 15px 0;"><a href="{random_btn_url}" target="_blank" style="display: inline-block; background-color: #6f42c1; color: white; text-decoration: none; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">{random_btn_text}</a></div>'

    html = f"""
<div style="font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 650px; margin: 15px auto; padding: 10px; background-color: #ffffff; color: #333333;">
  
  <!-- SEO/Feed Thumbnail Image -->
  <div style="text-align: center; margin-bottom: 20px;">
    <img src="{thumbnail_url}" alt="{match_name} Live Stream Preview" style="width: 100%; max-width: 600px; height: auto; border: 1px solid #cccccc; border-radius: 4px;" />
  </div>

  <!-- Ad Slot Top -->
  <div style="margin: 15px 0; text-align: center;">
    {ad_top}
  </div>

  <!-- Classic Goforsports Style Table -->
  <table border="0" cellpadding="0" cellspacing="0" style="background-color: white; border-collapse: collapse; border-spacing: 0px; border: 0.8pt solid rgb(0, 0, 0); color: black; line-height: 1.5; margin: 0px 0px 1.25rem; padding: 0px; text-align: center; vertical-align: baseline; width: 100%;">
    <tbody style="border: 0px; margin: 0px; padding: 0px; vertical-align: baseline;">
      <tr style="border: 0px; height: 50px; margin: 0px; padding: 0px; vertical-align: middle;">
        <td colspan="2" style="background: rgb(0, 102, 0); border: 0.7pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 100%;">
          <span style="color: white; font-size: large; font-weight: bold; text-transform: uppercase;">{team1} vs {team2}</span>
        </td>
      </tr>
      <tr style="background: rgb(0, 0, 0); border: 0px; height: 50px; margin: 1px; padding: 0px; vertical-align: middle;">
        <td colspan="1" style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;">
          <span style="color: white; font-weight: bold;">MATCH</span>
        </td>
        <td colspan="1" style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;">
          <span style="color: white; font-weight: bold;">SCHEDULE</span>
        </td>
      </tr>
      <tr style="border: 0px; height: 50px; margin: 1px; padding: 0px; vertical-align: middle;">
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; font-weight: bold; width: 50%;">MATCH</td>
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;">{team1} vs {team2}</td>
      </tr>
      <tr style="border: 0px; height: 50px; margin: 1px; padding: 0px; vertical-align: middle;">
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; font-weight: bold; width: 50%;">KICKOFF TIME</td>
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;" id="table-time-{safe_name}">Loading...</td>
      </tr>
      <tr style="border: 0px; height: 50px; margin: 1px; padding: 0px; vertical-align: middle;">
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; font-weight: bold; width: 50%;">QUALITY</td>
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;">1080p Full HD</td>
      </tr>
      <tr style="border: 0px; height: 50px; margin: 1px; padding: 0px; vertical-align: middle;">
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; font-weight: bold; width: 50%;">CHANNELS</td>
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;">Live Multi-Link Streams</td>
      </tr>
    </tbody>
  </table>

  <!-- Ticking Countdown Box -->
  <div style="background: #f7f9fa; border: 1px solid #e1e8ed; border-radius: 6px; padding: 15px; text-align: center; margin-bottom: 25px;">
    <div style="font-size: 11px; text-transform: uppercase; color: #555555; letter-spacing: 1.5px; margin-bottom: 8px; font-weight: bold;">LIVE STREAM STARTS IN</div>
    <div style="display: flex; justify-content: center; gap: 12px;">
      <div style="min-width: 45px;"><span id="days-{safe_name}" style="font-size: 22px; font-weight: bold; color: rgb(0, 102, 0);">00</span><div style="font-size: 9px; text-transform: uppercase; color: #777777;">Days</div></div>
      <div style="min-width: 45px;"><span id="hours-{safe_name}" style="font-size: 22px; font-weight: bold; color: rgb(0, 102, 0);">00</span><div style="font-size: 9px; text-transform: uppercase; color: #777777;">Hrs</div></div>
      <div style="min-width: 45px;"><span id="mins-{safe_name}" style="font-size: 22px; font-weight: bold; color: rgb(0, 102, 0);">00</span><div style="font-size: 9px; text-transform: uppercase; color: #777777;">Mins</div></div>
      <div style="min-width: 45px;"><span id="secs-{safe_name}" style="font-size: 22px; font-weight: bold; color: rgb(0, 102, 0);">00</span><div style="font-size: 9px; text-transform: uppercase; color: #777777;">Secs</div></div>
    </div>
  </div>

  <!-- CTA Watch Button -->
  <div style="text-align: center; margin-bottom: 25px;">
    <a href="{page_url}" style="display: inline-block; width: 100%; max-width: 550px; box-sizing: border-box; background: linear-gradient(180deg, #28a745 0%, #218838 100%); color: #ffffff; text-decoration: none; padding: 15px 20px; border-radius: 6px; font-size: 16px; font-weight: bold; border: 1px solid #1e7e34; text-transform: uppercase; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
      CLICK HERE TO WATCH LIVE STREAM
    </a>
  </div>

  {social_buttons_html}
  {random_button_html}

  <!-- Match Preview Content -->
  <div style="line-height: 1.6; font-size: 14px; color: #333333; margin-bottom: 25px; border-top: 1px solid #eeeeee; padding-top: 20px;">
    <h3 style="color: #111111; font-size: 16px; font-weight: bold; margin-top: 0; margin-bottom: 12px; border-left: 4px solid rgb(0, 102, 0); padding-left: 8px;">Match Analysis & Overview</h3>
    <p style="margin: 0 0 15px 0;">
      As <strong>{team1}</strong> prepares to clash with <strong>{team2}</strong>, sports analysts and football fans around the world are gearing up for a blockbuster kickoff. Both squads have shown outstanding tactical growth and consistency in their recent matchups, making this a pivotal meeting that could decide tournament standing.
    </p>
    <p style="margin: 0 0 15px 0;">
      {team1} will look to maximize their field advantage and control the tempo from the midfield. Meanwhile, {team2} boasts quick transition play and high conversion rates on counter-attacks, which will present a major test for the host's defensive backline. Expect a high-stakes, fast-paced match with tactical modifications from both managers.
    </p>
  </div>

  <!-- Ad Slot Bottom -->
  <div style="margin: 15px 0; text-align: center;">
    {ad_bottom}
  </div>

</div>

{social_script}

<script type="text/javascript">
(function() {{
  const matchSafe = "{safe_name}";
  const kickoff = new Date("{match_time_str}").getTime();
  
  const tblDisplay = document.getElementById("table-time-" + matchSafe);
  
  const options = {{ weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' }};
  const localFormatted = new Date("{match_time_str}").toLocaleDateString(undefined, options);
  
  if (tblDisplay) tblDisplay.textContent = localFormatted;

  const elDays = document.getElementById("days-" + matchSafe);
  const elHrs = document.getElementById("hours-" + matchSafe);
  const elMins = document.getElementById("mins-" + matchSafe);
  const elSecs = document.getElementById("secs-" + matchSafe);

  const t = setInterval(function() {{
    const diff = kickoff - new Date().getTime();
    if (diff <= 0) {{
      clearInterval(t);
      return;
    }}
    if (elDays) elDays.textContent = String(Math.floor(diff / (1000 * 60 * 60 * 24))).padStart(2, '0');
    if (elHrs) elHrs.textContent = String(Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60))).padStart(2, '0');
    if (elMins) elMins.textContent = String(Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60))).padStart(2, '0');
    if (elSecs) elSecs.textContent = String(Math.floor((diff % (1000 * 60)) / 1000)).padStart(2, '0');
  }}, 1000);
}})();
</script>
"""
    return html

def generate_page_html(config, match, safe_name):
    match_name = match["match_name"]
    widget_base = config.get("widget_base_url", "https://yourdomain.com/widget.html")
    widget_url = f"{widget_base}?match={requests.utils.quote(match_name)}"
    
    ad_top = config.get("ad_code_top") or DEFAULT_AD_TOP
    ad_bottom = config.get("ad_code_bottom") or DEFAULT_AD_POPUP

    # Randomized WhatsApp and Telegram group redirect buttons
    whatsapp_groups = config.get("whatsapp_groups", [])
    telegram_channels = config.get("telegram_channels", [])
    
    wa_json = json.dumps(whatsapp_groups)
    tg_json = json.dumps(telegram_channels)
    
    social_buttons_html = ""
    social_script = ""
    
    if whatsapp_groups or telegram_channels:
        social_buttons_html += '<div style="text-align: center; margin: 15px 0; display: flex; justify-content: center; gap: 12px; flex-wrap: wrap;">'
        if whatsapp_groups:
            social_buttons_html += '<button onclick="openRandomWaGroup()" style="display: inline-block; background-color: #25d366; color: white; border: none; cursor: pointer; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">Join WhatsApp Group</button>'
        if telegram_channels:
            social_buttons_html += '<button onclick="openRandomTgGroup()" style="display: inline-block; background-color: #0088cc; color: white; border: none; cursor: pointer; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">Join Telegram Channel</button>'
        social_buttons_html += '</div>'
        
        social_script = f"""
<script type="text/javascript">
(function() {{
  const waPool = {wa_json};
  const tgPool = {tg_json};
  
  window.openRandomWaGroup = function() {{
    if (!waPool || waPool.length === 0) return;
    const idx = Math.floor(Math.random() * waPool.length);
    window.open(waPool[idx], '_blank');
  }};
  
  window.openRandomTgGroup = function() {{
    if (!tgPool || tgPool.length === 0) return;
    const idx = Math.floor(Math.random() * tgPool.length);
    window.open(tgPool[idx], '_blank');
  }};
}})();
</script>
"""

    # Optional random button
    random_button_html = ""
    random_btn_text = config.get("random_btn_text")
    random_btn_url = config.get("random_btn_url")
    if random_btn_text and random_btn_url:
        random_button_html = f'<div style="text-align: center; margin: 15px 0;"><a href="{random_btn_url}" target="_blank" style="display: inline-block; background-color: #6f42c1; color: white; text-decoration: none; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">{random_btn_text}</a></div>'

    html = f"""
<div style="font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 680px; margin: 10px auto; padding: 15px; background-color: #ffffff; color: #333333; border: 1px solid #dddddd; border-radius: 6px;">
  
  <div style="text-align: center; font-size: 20px; font-weight: bold; color: #111111; margin-bottom: 15px;">
    {match_name} - Live Stream
  </div>

  <!-- Ad Slot Top -->
  <div style="margin: 15px 0; text-align: center;">
    {ad_top}
  </div>

  <!-- Widget Container / Player Frame (Initial countdown widget) -->
  <div id="player-frame-container" style="background: #000; border-radius: 6px; overflow: hidden; border: 1px solid #cccccc; box-shadow: 0 4px 15px rgba(0,0,0,0.15);">
    <iframe src="{widget_url}" width="100%" height="480px" frameborder="0" allowfullscreen style="display: block; background: #000; border: none;"></iframe>
  </div>

  {social_buttons_html}
  {random_button_html}

  <!-- Ad Slot Bottom -->
  <div style="margin: 15px 0; text-align: center;">
    {ad_bottom}
  </div>

</div>

{social_script}
"""
    return html

def generate_post_html(config, match, safe_name=None):
    return render_preview_post(config, match)


def generate_page_html(config, match, safe_name=None):
    return render_streaming_page(config, match, state="upcoming")


def can_refresh_portal_content(match, scheduler_config, now):
    if match.get("new_blog_iframe_set"):
        return False
    try:
        match_time = parse_match_time(match["match_time"])
    except Exception:
        return True

    start_offset = int(scheduler_config.get("active_window_start_minutes", 15))
    end_hours = float(scheduler_config.get("active_window_end_hours", 3))
    run_start = match_time - timedelta(minutes=start_offset)
    run_end = match_time + timedelta(hours=end_hours)
    return not (run_start <= now <= run_end)


def portal_mode(match):
    return str(match.get("portal_mode") or "auto").strip().lower()


def is_manual_portal_match(match):
    return portal_mode(match) in ("manual", "skip", "disabled") or match.get("portal_managed") is False


def log_dry_run(message):
    print(f"[dry-run] {message}")


def rendered_content_changed(match, key, html):
    hashes = match.get("content_hashes") if isinstance(match.get("content_hashes"), dict) else {}
    return hashes.get(key) != content_hash(html)


def record_content_hash(match, key, html):
    hashes = match.get("content_hashes") if isinstance(match.get("content_hashes"), dict) else {}
    hashes[key] = content_hash(html)
    match["content_hashes"] = hashes


def resolve_ist_date_selector(selector, now):
    selector = str(selector or "").strip().lower()
    today = now.astimezone(IST).date()
    if selector == "today":
        return today
    if selector == "tomorrow":
        return today + timedelta(days=1)
    try:
        return datetime.strptime(selector, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Use today, tomorrow, or YYYY-MM-DD for --for-ist-date.") from exc


def match_in_ist_date(match, target_date):
    try:
        return parse_match_time(match["match_time"]).astimezone(IST).date() == target_date
    except Exception:
        return False


def match_within_hours(match, now, hours):
    try:
        match_time = parse_match_time(match["match_time"])
    except Exception:
        return False
    return now <= match_time <= now + timedelta(hours=hours)


def match_in_precreate_scope(match, target_ist_date, within_hours, now):
    if match.get("status") == "completed":
        return False
    if match.get("status", "pending") not in ("pending", "processing", "active", "live"):
        return False
    if target_ist_date and not match_in_ist_date(match, target_ist_date):
        return False
    if within_hours is not None and not match_within_hours(match, now, within_hours):
        return False
    if is_manual_portal_match(match):
        return False
    return True


def preview_publish_overrides(schedule, target_ist_date, within_hours, now, enabled=True):
    if not enabled:
        return {}
    
    # Use the start of the current day as base_date to keep upcoming matches' published dates fresh (in 2026)
    base_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    overrides = {}
    for match in schedule:
        if not match_in_precreate_scope(match, target_ist_date, within_hours, now):
            continue
        try:
            kickoff = parse_match_time(match["match_time"])
        except Exception:
            kickoff = now + timedelta(days=365)
        
        # Reflect across base_date: earlier kickoff -> smaller delta -> larger published date
        delta = kickoff - base_date
        published_dt = base_date - delta
        
        overrides[match_key(match)] = published_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        
    return overrides


def published_date_mismatch(config, access_token, post_id, desired_pub):
    if not desired_pub or not post_id:
        return False
    try:
        blog_id = config.get("blog_id")
        url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/{post_id}"
        headers = {"Authorization": f"Bearer {access_token}"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            current_pub = res.json().get("published")
            if current_pub:
                # Convert both dates to ISO strings with offset for comparison
                curr_s = current_pub[:-1] + "+00:00" if current_pub.endswith("Z") else current_pub
                des_s = desired_pub[:-1] + "+00:00" if desired_pub.endswith("Z") else desired_pub
                curr_dt = datetime.fromisoformat(curr_s)
                des_dt = datetime.fromisoformat(des_s)
                if abs((curr_dt - des_dt).total_seconds()) > 2:
                    print(f"[*] Published date mismatch for post {post_id}: current={current_pub}, desired={desired_pub}")
                    return True
    except Exception as e:
        print(f"[!] Desired published date check failed: {e}")
    return False


def preview_post_refresh_needed(post_changed, pub_mismatch):
    return bool(post_changed or pub_mismatch)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Create/update portal Blogger preview posts and streaming pages safely.")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without creating, updating, saving schedule, or generating thumbnails.")
    parser.add_argument("--create-missing-only", action="store_true", help="Only create missing portal items. Existing Page/Post content is not refreshed.")
    parser.add_argument("--refresh-existing", action="store_true", help="Refresh existing auto-managed portal Page/Post content when rendered content changes.")
    parser.add_argument("--for-ist-date", help="Only process matches on an IST calendar date: today, tomorrow, or YYYY-MM-DD.")
    parser.add_argument("--within-hours", type=float, help="Only process matches kicking off within the next N hours from now UTC.")
    args = parser.parse_args()

    if args.create_missing_only and args.refresh_existing:
        print("[-] Choose either --create-missing-only or --refresh-existing, not both.")
        sys.exit(2)
    if args.for_ist_date and args.within_hours is not None:
        print("[-] Choose either --for-ist-date or --within-hours, not both.")
        sys.exit(2)
    if args.within_hours is not None and args.within_hours <= 0:
        print("[-] --within-hours must be greater than zero.")
        sys.exit(2)

    now = datetime.now(timezone.utc)
    target_ist_date = None
    if args.for_ist_date:
        try:
            target_ist_date = resolve_ist_date_selector(args.for_ist_date, now)
        except ValueError as exc:
            print(f"[-] {exc}")
            sys.exit(2)

    automation_config = load_automation_config()
    config = get_portal_blog_config(automation_config)
    scheduler_config = get_scheduler_config(automation_config)
    fixture_config = get_fixture_api_config(automation_config)
    paths = ensure_runtime_dirs(automation_config)
    refresh_existing = bool(args.refresh_existing) or (
        not args.create_missing_only and bool(scheduler_config.get("portal_refresh_on_metadata_changes", True))
    )

    print("[*] Starting Blogger precreate (Posts & Pages) pipeline...")
    if args.dry_run:
        print("[*] Dry run enabled: no Blogger writes, thumbnail writes, or schedule saves will be performed.")
    if refresh_existing:
        print("[*] Existing auto-managed portal content may be refreshed when rendered content changes.")
    else:
        print("[*] Safe mode: existing portal content will be linked/recorded but not refreshed.")
    if target_ist_date:
        print(f"[*] Scope: matches on IST date {target_ist_date.isoformat()} only.")
    elif args.within_hours is not None:
        print(f"[*] Scope: matches within the next {args.within_hours:g} hour(s) from now UTC.")

    if not has_oauth(config):
        print("[-] Portal Blogger OAuth is missing. Configure portal_blog in master_config.json or run auth_blogger.py for new_blogger_config.json.")
        sys.exit(1)

    schedule = load_schedule(automation_config)
    if not schedule:
        print("[-] Match schedule empty.")
        sys.exit(1)

    print("[*] Fetching access token...")
    try:
        access_token = get_access_token(config)
    except Exception as e:
        print(f"[-] Access token error: {e}")
        sys.exit(1)
        
    print("[+] OAuth token verified.")
    changed = False
    if args.dry_run:
        archived = []
    else:
        schedule, archived = archive_completed_matches(schedule, automation_config, scheduler_config, now)
        if archived:
            print(f"[*] Archived {len(archived)} completed match(es) out of the active schedule.")
            changed = True

    scoped_count = 0
    skipped_by_scope = 0
    publish_overrides = preview_publish_overrides(
        schedule,
        target_ist_date,
        args.within_hours,
        now,
        enabled=config.get("bump_preview_published_on_refresh", True),
    )

    for match in schedule:
        if match.get("status") == "completed":
            continue

        if match.get("status", "pending") in ("pending", "processing", "active", "live"):
            if scheduler_config.get("fixture_first_only", True) and not schedule_match_allowed(match, fixture_config):
                skipped_by_scope += 1
                continue
            if target_ist_date and not match_in_ist_date(match, target_ist_date):
                skipped_by_scope += 1
                continue
            if args.within_hours is not None and not match_within_hours(match, now, args.within_hours):
                skipped_by_scope += 1
                continue
            scoped_count += 1

            if is_manual_portal_match(match):
                print(f"\n[*] Skipping manual portal match: {match['match_name']}")
                continue

            match["match_key"] = match.get("match_key") or match_key(match)
            safe_name = slugify_match_name(match["match_name"])
            can_refresh = can_refresh_portal_content(match, scheduler_config, now)
            if refresh_match_metadata(match, scheduler_config, now, active=False):
                print(f"[*] Metadata checked/updated for: {match['match_name']}")
                changed = True

            # Step 1: Generate Match-Specific Thumbnail image before rendering any Blogger HTML.
            img_path = thumbnail_path(automation_config, match)
            if thumbnail_needs_generation(img_path):
                print(f"[*] Generating custom thumbnail for: {match['match_name']}...")
                if args.dry_run:
                    log_dry_run(f"Would generate thumbnail: {img_path}")
                    changed = True
                else:
                    t1, t2 = split_teams(match["match_name"])
                    generate_thumbnail(
                        t1,
                        t2,
                        match["match_time"],
                        img_path,
                        league=match.get("match_genre") or match.get("genre") or match.get("league") or match.get("competition") or config.get("default_match_genre") or config.get("default_league", ""),
                        channel=get_channel_info(match, config),
                    )

            if sanitize_thumbnail_fields(match, automation_config, config, scheduler_config):
                print(f"[*] Removed blocked source thumbnail for: {match['match_name']}")
                changed = True

            render_match = with_thumbnail_src(automation_config, config, match)
            if not render_match.get("thumbnail_url"):
                print(f"[!] No public per-match thumbnail URL configured for: {match['match_name']}")
            if refresh_thumbnail_url_from_sources(match, scheduler_config):
                print(f"[*] Public thumbnail URL found for: {match['match_name']}")
                changed = True
                render_match = with_thumbnail_src(automation_config, config, match)
            if refresh_lineups_for_match(match, scheduler_config, now, active=False):
                print(f"[*] Lineup info checked/updated for: {match['match_name']}")
                changed = True
                render_match = with_thumbnail_src(automation_config, config, match)
            
            # Step 2: Create or Refresh Blogger PAGE (Stream Player Page)
            page_title = streaming_page_title(render_match, config)
            page_id = match.get("new_blogger_page_id")
            if config.get("prefer_existing_pages_on_refresh", True):
                try:
                    existing_page_id, existing_page_url = find_existing_blogger_page(config, access_token, page_title, match["match_name"])
                except Exception as e:
                    existing_page_id, existing_page_url = None, None
                    print(f"[!] Existing Page lookup failed for {match['match_name']}: {e}")
                if existing_page_id and (existing_page_id != page_id or existing_page_url != match.get("new_blogger_page_url")):
                    print(f"[*] Using existing stream Page for: {match['match_name']} | {existing_page_url}")
                    page_id = existing_page_id
                    match["new_blogger_page_id"] = existing_page_id
                    match["new_blogger_page_url"] = existing_page_url
                    match["new_blog_iframe_set"] = False
                    match["new_blog_prepare_set"] = False
                    render_match = with_thumbnail_src(automation_config, config, match)
                    page_title = streaming_page_title(render_match, config)
                    changed = True
            page_html = generate_page_html(config, render_match, safe_name)
            page_changed = rendered_content_changed(match, "stream_page", page_html)
            
            if not page_id:
                print(f"\n[*] Checking stream Page for: {match['match_name']}...")
                try:
                    page_written = False
                    existing_page_id, existing_page_url = find_existing_blogger_page(config, access_token, page_title, match["match_name"])
                    if existing_page_id:
                        print(f"[*] Found existing stream Page. ID: {existing_page_id}")
                        page_id = existing_page_id
                        page_url = existing_page_url
                        existing_title_match = dict(match)
                        existing_title_match["new_blogger_page_url"] = page_url
                        existing_title_match = with_thumbnail_src(automation_config, config, existing_title_match)
                        page_title = streaming_page_title(existing_title_match, config)
                        page_html = generate_page_html(config, existing_title_match, safe_name)
                        page_changed = rendered_content_changed(match, "stream_page", page_html)
                        if refresh_existing and can_refresh and page_changed:
                            if args.dry_run:
                                log_dry_run(f"Would refresh existing stream Page for {match['match_name']} ({page_id}).")
                                changed = True
                            else:
                                page_url = update_blogger_page(config, access_token, page_id, page_title, page_html)
                                record_content_hash(match, "stream_page", page_html)
                                page_written = True
                        else:
                            print("[*] Existing stream Page content left unchanged.")
                    else:
                        if args.dry_run:
                            log_dry_run(f"Would create stream Page for {match['match_name']}.")
                            changed = True
                            page_id, page_url = "", ""
                        else:
                            page_id, page_url = create_stream_blogger_page(config, access_token, render_match, page_title, page_html)
                            page_written = True
                    if page_id and not args.dry_run:
                        match["new_blogger_page_id"] = page_id
                        match["new_blogger_page_url"] = page_url
                        match["new_blog_iframe_set"] = False
                        match["new_blog_prepare_set"] = False
                        if page_written:
                            record_content_hash(match, "stream_page", page_html)
                        changed = True
                    elif page_id:
                        log_dry_run(f"Would record stream Page ID/URL for {match['match_name']}: {page_id} | {page_url}")
                        changed = True
                    if page_id:
                        print(f"[+] Page ready. ID: {page_id} | URL: {page_url}")
                except Exception as e:
                    print(f"[-] Page creation failed: {e}")
                    continue
            elif not can_refresh:
                print(f"\n[*] Skipping stream Page refresh for active/live match: {match['match_name']}")
            elif not refresh_existing:
                print(f"\n[*] Existing stream Page left unchanged for: {match['match_name']} (ID: {page_id})")
            elif not page_changed:
                print(f"\n[*] Existing stream Page already current for: {match['match_name']} (ID: {page_id})")
            else:
                print(f"\n[*] Refreshing existing stream Page for: {match['match_name']} (ID: {page_id})...")
                try:
                    if args.dry_run:
                        log_dry_run(f"Would refresh stream Page for {match['match_name']} ({page_id}).")
                        changed = True
                    else:
                        page_url = update_blogger_page(config, access_token, page_id, page_title, page_html)
                        match["new_blogger_page_url"] = page_url
                        match["new_blog_iframe_set"] = False
                        match["new_blog_prepare_set"] = False
                        record_content_hash(match, "stream_page", page_html)
                        print(f"[+] Page content refreshed. URL: {page_url}")
                        changed = True
                except Exception as e:
                    print(f"[-] Page refresh failed: {e}")

            # Step 3: Create or Refresh Blogger POST (Preview Post)
            render_match = with_thumbnail_src(automation_config, config, match)
            post_title = preview_post_title(render_match, config)
            post_html = generate_post_html(config, render_match, safe_name)
            post_id = match.get("new_blogger_post_id")
            post_changed = rendered_content_changed(match, "preview_post", post_html)
            
            if not post_id:
                print(f"[*] Checking preview Post for: {match['match_name']}...")
                try:
                    post_written = False
                    existing_post_id, existing_post_url = find_existing_blogger_post(config, access_token, post_title, match["match_name"])
                    if existing_post_id:
                        print(f"[*] Found existing preview Post. ID: {existing_post_id}")
                        post_id = existing_post_id
                        post_url = existing_post_url
                        post_changed = rendered_content_changed(match, "preview_post", post_html)
                        pub_mismatch = refresh_existing and published_date_mismatch(config, access_token, post_id, publish_overrides.get(match["match_key"]))
                        if refresh_existing and can_refresh and (post_changed or pub_mismatch):
                            if args.dry_run:
                                log_dry_run(f"Would refresh existing preview Post for {match['match_name']} ({post_id}).")
                                changed = True
                            else:
                                post_url = update_blogger_post(
                                    config,
                                    access_token,
                                    post_id,
                                    post_title,
                                    post_html,
                                    published=publish_overrides.get(match["match_key"]),
                                    preserve_existing_thumbnail=True,
                                )
                                record_content_hash(match, "preview_post", post_html)
                                post_written = True
                        else:
                            print("[*] Existing preview Post content left unchanged.")
                    else:
                        if args.dry_run:
                            log_dry_run(f"Would create preview Post for {match['match_name']}.")
                            changed = True
                            post_id, post_url = "", ""
                        else:
                            post_id, post_url = create_blogger_post(
                                config,
                                access_token,
                                post_title,
                                post_html,
                                published=publish_overrides.get(match["match_key"])
                            )

                            post_written = True
                    if post_id and not args.dry_run:
                        match["new_blogger_post_id"] = post_id
                        match["new_blogger_post_url"] = post_url
                        if post_written:
                            record_content_hash(match, "preview_post", post_html)
                        changed = True
                    elif post_id:
                        log_dry_run(f"Would record preview Post ID/URL for {match['match_name']}: {post_id} | {post_url}")
                        changed = True
                    if post_id:
                        print(f"[+] Post ready. ID: {post_id} | URL: {post_url}")
                except Exception as e:
                    print(f"[-] Post creation failed: {e}")
            elif not can_refresh:
                print(f"[*] Skipping preview Post refresh for active/live match: {match['match_name']}")
            elif not refresh_existing:
                print(f"[*] Existing preview Post left unchanged for: {match['match_name']} (ID: {post_id})")
            else:
                pub_mismatch = published_date_mismatch(config, access_token, post_id, publish_overrides.get(match["match_key"]))
                if not preview_post_refresh_needed(post_changed, pub_mismatch):
                    print(f"[*] Existing preview Post already current for: {match['match_name']} (ID: {post_id})")
                    continue
                else:
                    print(f"[*] Refreshing existing preview Post for: {match['match_name']} (ID: {post_id})...")
                try:
                    if args.dry_run:
                        log_dry_run(f"Would refresh preview Post for {match['match_name']} ({post_id}).")
                        changed = True
                    else:
                        post_url = update_blogger_post(
                            config,
                            access_token,
                            post_id,
                            post_title,
                            post_html,
                            published=publish_overrides.get(match["match_key"]),
                            preserve_existing_thumbnail=True,
                        )
                        match["new_blogger_post_url"] = post_url
                        record_content_hash(match, "preview_post", post_html)
                        print(f"[+] Post content refreshed. URL: {post_url}")
                        changed = True
                except Exception as e:
                    print(f"[-] Post refresh failed: {e}")

    if (target_ist_date or args.within_hours is not None) and scoped_count == 0:
        print(f"\n[*] No matches found in the selected precreate scope. Skipped {skipped_by_scope} out-of-scope match(es).")

    if changed and not args.dry_run:
        save_schedule(schedule, automation_config)
        print("\n[+] Done. Schedule file updated with daily Page and Post details.")
    elif changed and args.dry_run:
        print("\n[+] Dry run complete. Schedule was not saved.")
    else:
        print("\n[+] No actions needed. All preview posts/pages are up to date.")

if __name__ == "__main__":
    main()
