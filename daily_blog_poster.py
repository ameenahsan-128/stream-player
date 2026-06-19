#!/usr/bin/env python3
"""
daily_blog_poster.py
====================
Standalone daily football news blog poster for Blogger.
Runs as a SEPARATE cronjob — completely independent of the stream pipeline.

Usage:
    python3 daily_blog_poster.py [--dry-run] [--config master_config.json]

Config:  Uses the `daily_blog` section in master_config.json (or its own config file).
Blog:    https://www.blogger.com/blog/posts/6642954258605411314  (blog_id: 6642954258605411314)
OAuth:   Reuses the portal_blog OAuth credentials (client_id/client_secret/refresh_token).

Image strategy:
    Blogger API does NOT support direct image upload.
    We host the AI-generated thumbnail as a base64 data-URI embedded in the post HTML,
    or upload to a free image host (imgbb). If IMGBB_API_KEY is set in the config or env,
    the image is uploaded there and referenced as a proper <img src="https://..."> URL,
    which makes it appear as the Blogger post thumbnail in feeds.
"""

import argparse
import base64
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from io import BytesIO

import requests

# ─── Constants ───────────────────────────────────────────────────────────────

DAILY_BLOG_ID = "6642954258605411314"
CONFIG_FILE = "master_config.json"
STATE_FILE = "data/daily_blog_state.json"
LOG_FILE = "data/daily_blog.log"

# Football news RSS feeds (no API key needed)
RSS_FEEDS = [
    "https://www.goal.com/feeds/en/news",
    "https://www.90min.com/feed",
    "https://www.skysports.com/rss/12040",     # Sky Sports Football
    "https://www.theguardian.com/football/rss",
    "https://www.espn.com/espn/rss/soccer/news",
    "https://theathletic.com/rss/",
    "https://www.bbc.com/sport/football/rss.xml",
    "https://www.football365.com/feed",
]

OPENAI_MODEL = "gpt-4o-mini"
GEMINI_MODEL = "gemini-1.5-flash"  # free tier: 1500 requests/day

IST = timezone(timedelta(hours=5, minutes=30))


# ─── Logging ─────────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ─── Config ───────────────────────────────────────────────────────────────────

def load_config(config_path=CONFIG_FILE):
    if not os.path.exists(config_path):
        log(f"Config file not found: {config_path}", "ERROR")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_daily_blog_config(config):
    """
    Returns the daily_blog section. Falls back to portal_blog OAuth creds
    if daily_blog doesn't define its own OAuth.
    """
    portal = config.get("portal_blog", {})
    daily = config.get("daily_blog", {})

    merged = {
        "blog_id": DAILY_BLOG_ID,
        "client_id": portal.get("client_id", ""),
        "client_secret": portal.get("client_secret", ""),
        "refresh_token": portal.get("refresh_token", ""),
        "openai_api_key": "",
        "gemini_api_key": "",
        "imgbb_api_key": "",
        "post_hour_utc": 6,        # publish at 06:00 UTC daily
        "topics": ["FIFA World Cup 2026", "football", "soccer"],
        "post_labels": ["Football", "FIFA World Cup 2026", "Football News"],
    }
    merged.update({k: v for k, v in daily.items() if v})
    # Also check env overrides
    merged["openai_api_key"] = merged.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
    merged["gemini_api_key"] = merged.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    merged["imgbb_api_key"] = merged.get("imgbb_api_key") or os.environ.get("IMGBB_API_KEY", "")
    return merged


# ─── State (dedup) ────────────────────────────────────────────────────────────

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"posted_titles": [], "last_post_date": ""}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def already_posted_today(state):
    today = datetime.now(IST).strftime("%Y-%m-%d")
    return state.get("last_post_date") == today


# ─── OAuth / Blogger API ──────────────────────────────────────────────────────

def get_access_token(cfg):
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": cfg["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {resp.text[:200]}")
    return token


def create_blogger_post(cfg, access_token, title, html_content, labels=None):
    blog_id = cfg["blog_id"]
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "kind": "blogger#post",
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content,
    }
    if labels:
        payload["labels"] = labels
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code == 429:
        raise RuntimeError(f"Blogger rate limit: {resp.text[:200]}")
    resp.raise_for_status()
    data = resp.json()
    return data.get("id"), data.get("url")


# ─── News Fetching (RSS, no API key) ─────────────────────────────────────────

def fetch_rss_items(feed_url, max_items=5, timeout=10):
    """Parse RSS/Atom feed, return list of {title, summary, link, pubdate}."""
    try:
        resp = requests.get(feed_url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return []
        xml = resp.text

        items = []
        # Find all <item> or <entry> blocks
        blocks = re.findall(r"<(?:item|entry)>(.*?)</(?:item|entry)>", xml, re.DOTALL)
        for block in blocks[:max_items]:
            title_m = re.search(r"<title[^>]*>(.*?)</title>", block, re.DOTALL)
            desc_m = re.search(r"<(?:description|summary|content)[^>]*>(.*?)</(?:description|summary|content)>", block, re.DOTALL)
            link_m = re.search(r"<link[^>]*>(https?://[^<]+)</link>|<link[^>]+href=[\"'](https?://[^\"']+)[\"']", block, re.DOTALL)
            pub_m = re.search(r"<(?:pubDate|published|updated)[^>]*>(.*?)</(?:pubDate|published|updated)>", block, re.DOTALL)

            title = _strip_cdata(_strip_tags(title_m.group(1) if title_m else "")).strip()
            desc = _strip_cdata(_strip_tags(desc_m.group(1) if desc_m else "")).strip()[:500]
            link = (link_m.group(1) or link_m.group(2)) if link_m else ""
            pub = (pub_m.group(1) if pub_m else "").strip()[:100]

            if title:
                items.append({"title": title, "summary": desc, "link": link, "pubdate": pub})
        return items
    except Exception as e:
        log(f"RSS fetch failed for {feed_url}: {e}", "WARN")
        return []


def _strip_tags(text):
    return re.sub(r"<[^>]+>", " ", text or "")


def _strip_cdata(text):
    return re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text or "", flags=re.DOTALL)


def gather_top_football_news(max_total=8):
    """Gather headlines from multiple RSS feeds, deduplicate by title."""
    seen = set()
    all_items = []
    for feed in RSS_FEEDS:
        items = fetch_rss_items(feed, max_items=4)
        for item in items:
            key = item["title"].lower()[:60]
            if key not in seen and len(all_items) < max_total:
                seen.add(key)
                all_items.append(item)
    log(f"Gathered {len(all_items)} news items from RSS feeds")
    return all_items


# ─── AI Content Generation ────────────────────────────────────────────────────

def _build_ai_prompt(news_items, cfg):
    """Shared prompt builder for any AI backend."""
    today_str = datetime.now(IST).strftime("%B %d, %Y")
    topics = ", ".join(cfg.get("topics", ["football"]))
    headlines_block = "\n".join(
        f"{i+1}. {item['title']} — {item.get('summary', '')[:200]}"
        for i, item in enumerate(news_items)
    )
    system = (
        "You are a professional football journalist writing daily blog posts. "
        "Write engaging, SEO-friendly content about football/soccer news. "
        "Your writing is enthusiastic, knowledgeable, and accessible to all fans."
    )
    user = f"""Today is {today_str}. Here are today's top football news headlines:

{headlines_block}

Write a high-quality football news roundup blog post:
1. First line: TITLE: <compelling SEO title>
2. Then clean HTML body using only <h2>, <p>, <strong>, <em>, <ul>, <li> tags
3. Structure: intro → 3-4 story sections with <h2> headings → conclusion
4. Focus: {topics}
5. Tone: professional yet passionate football journalism
6. Length: ~600-800 words"""
    return system, user, today_str


def _parse_ai_response(text, today_str):
    """Extract title and body from TITLE: prefixed AI response."""
    text = text.strip()
    title, body = "", text
    if text.upper().startswith("TITLE:"):
        parts = text.split("\n", 1)
        title = parts[0][6:].strip()  # strip "TITLE:"
        body = parts[1].strip() if len(parts) > 1 else ""
    if not title:
        title = f"Football News Roundup — {today_str}"
    return title, body


def generate_blog_content_gemini(api_key, news_items, cfg):
    """Use Google Gemini (free tier: 1500 req/day) to write the blog post."""
    if not api_key:
        raise RuntimeError("No Gemini API key configured.")
    system, user, today_str = _build_ai_prompt(news_items, cfg)
    full_prompt = f"{system}\n\n{user}"
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.75},
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_ai_response(text, today_str)


def generate_blog_content_openai(api_key, news_items, cfg):
    """Use OpenAI gpt-4o-mini to write the blog post."""
    if not api_key:
        raise RuntimeError("No OpenAI API key configured.")
    system, user, today_str = _build_ai_prompt(news_items, cfg)
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 2000,
            "temperature": 0.75,
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return _parse_ai_response(text, today_str)


def fallback_blog_content(news_items):
    """Fallback when no OpenAI key: generate a plain roundup from RSS headlines."""
    today_str = datetime.now(IST).strftime("%B %d, %Y")
    title = f"Football News Roundup — {today_str}"

    items_html = ""
    for item in news_items[:6]:
        link_html = f' <a href="{item["link"]}" target="_blank" rel="noopener">Read more</a>' if item.get("link") else ""
        items_html += f"""
<h2>{item['title']}</h2>
<p>{item.get('summary', 'Check out the latest developments in this story.')}{link_html}</p>
"""

    body = f"""
<p>Welcome to today's football news roundup! Here's everything happening in the world of football on {today_str}.</p>
{items_html}
<p>Stay tuned for more football updates and live stream coverage throughout the day.</p>
"""
    return title, body


# ─── Thumbnail Generation (PIL) ───────────────────────────────────────────────

def generate_blog_thumbnail(title, today_str, output_path):
    """Generate a visually rich blog post thumbnail (1200x630 — OG standard)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log("Pillow not available, skipping thumbnail generation", "WARN")
        return None

    W, H = 1200, 630
    img = Image.new("RGB", (W, H), "#0a0f1e")
    draw = ImageDraw.Draw(img)

    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    font_paths_regular = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]

    def load_font(size, bold=True):
        paths = font_paths if bold else font_paths_regular
        for p in paths:
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
        return ImageFont.load_default()

    # Background gradient simulation via rectangles
    for i in range(H):
        ratio = i / H
        r = int(10 + 20 * ratio)
        g = int(15 + 15 * ratio)
        b = int(30 + 40 * ratio)
        draw.line([(0, i), (W, i)], fill=(r, g, b))

    # Green accent top bar
    draw.rectangle([0, 0, W, 8], fill="#00c853")

    # Decorative circles
    draw.ellipse([-60, -60, 220, 220], outline="#00c85322", width=40)
    draw.ellipse([W - 200, H - 200, W + 60, H + 60], outline="#ffffff11", width=30)

    # Football emoji-style circle pattern
    draw.ellipse([W - 140, 20, W - 20, 140], fill="#ffffff08", outline="#ffffff15", width=2)

    # "FOOTBALL NEWS" badge
    badge_w = 320
    badge_x = (W - badge_w) // 2
    draw.rounded_rectangle([badge_x, 40, badge_x + badge_w, 92], radius=24, fill="#00c853")
    badge_font = load_font(28)
    badge_text = "⚽  FOOTBALL NEWS  ⚽"
    try:
        bw = draw.textlength(badge_text, font=badge_font)
    except Exception:
        bw = badge_w - 20
    draw.text(((W - bw) / 2, 52), badge_text, font=badge_font, fill="#0a0f1e")

    # Title text (word-wrapped)
    title_font = load_font(52)
    max_w = W - 100
    words = title.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        try:
            tw = draw.textlength(test, font=title_font)
        except Exception:
            tw = len(test) * 30
        if tw <= max_w:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    line_height = 62
    total_h = len(lines) * line_height
    y_start = (H - total_h) // 2 - 20

    for i, line in enumerate(lines[:4]):
        try:
            tw = draw.textlength(line, font=title_font)
        except Exception:
            tw = len(line) * 30
        x = (W - tw) / 2
        y = y_start + i * line_height
        # Shadow
        draw.text((x + 2, y + 2), line, font=title_font, fill="#00000080")
        draw.text((x, y), line, font=title_font, fill="#ffffff")

    # Date strip at bottom
    draw.rectangle([0, H - 70, W, H], fill="#00000060")
    date_font = load_font(28, bold=False)
    date_text = f"📅  {today_str}  |  goforsports.net"
    try:
        dw = draw.textlength(date_text, font=date_font)
    except Exception:
        dw = W - 100
    draw.text(((W - dw) / 2, H - 50), date_text, font=date_font, fill="#cccccc")

    # Bottom green accent
    draw.rectangle([0, H - 6, W, H], fill="#00c853")

    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    img.save(output_path, "JPEG", quality=92, optimize=True)
    log(f"Thumbnail saved: {output_path}")
    return output_path


# ─── Image Hosting ────────────────────────────────────────────────────────────

def upload_to_imgbb(image_path, api_key):
    """Upload image to imgbb.com (free), return public URL."""
    try:
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        resp = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": api_key, "image": encoded, "expiration": 0},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        url = data.get("data", {}).get("url", "")
        log(f"Image uploaded to imgbb: {url}")
        return url
    except Exception as e:
        log(f"imgbb upload failed: {e}", "WARN")
        return ""


def image_to_data_uri(image_path, max_width=900):
    """Fallback: embed image as base64 data URI."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)), Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.save(buf, "JPEG", quality=80, optimize=True)
            enc = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{enc}"
    except Exception as e:
        log(f"data URI conversion failed: {e}", "WARN")
        return ""


# ─── HTML Post Assembly ───────────────────────────────────────────────────────

def build_post_html(title, body_html, thumbnail_url, news_items, today_str):
    """Wrap AI-generated body in a professional blog post HTML layout."""

    sources_html = ""
    if news_items:
        links = [
            f'<li><a href="{item["link"]}" target="_blank" rel="noopener">{item["title"][:80]}</a></li>'
            for item in news_items[:5]
            if item.get("link")
        ]
        if links:
            sources_html = f"""
<div style="border-top:2px solid #e8e8e8; margin-top:30px; padding-top:18px;">
  <h3 style="color:#555; font-size:14px; text-transform:uppercase; letter-spacing:1px;">📰 Sources &amp; Further Reading</h3>
  <ul style="color:#666; font-size:13px; line-height:1.8;">{''.join(links)}</ul>
</div>"""

    thumb_html = ""
    if thumbnail_url:
        thumb_html = f"""
<div style="text-align:center; margin:0 0 24px;">
  <img src="{thumbnail_url}" alt="{title}" style="width:100%; max-width:700px; height:auto; border-radius:8px; box-shadow:0 4px 16px rgba(0,0,0,0.15);" />
</div>"""

    return f"""<div style="font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif; max-width:720px; margin:0 auto; padding:10px; color:#1a1a2e; line-height:1.7;">

<!-- Header Badge -->
<div style="background:linear-gradient(135deg,#00c853,#00897b); color:white; text-align:center; padding:12px 20px; border-radius:8px; margin-bottom:24px; font-size:13px; font-weight:700; letter-spacing:2px; text-transform:uppercase;">
  ⚽ Daily Football News &nbsp;|&nbsp; {today_str}
</div>

{thumb_html}

<!-- Article Body -->
<div style="font-size:16px; color:#222;">
{body_html}
</div>

{sources_html}

<!-- Footer CTA -->
<div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; text-align:center; padding:20px; margin-top:30px;">
  <p style="margin:0 0 12px; font-weight:700; color:#065f46; font-size:15px;">🔴 Watch Live Football Streams</p>
  <a href="https://www.blogger.com/blog/posts/4927968984731236030" target="_blank" rel="noopener"
     style="display:inline-block; background:#00c853; color:white; text-decoration:none; padding:11px 28px; border-radius:6px; font-weight:800; font-size:14px; letter-spacing:.5px;">
    View Live Streams →
  </a>
</div>

<p style="text-align:center; color:#999; font-size:12px; margin-top:20px;">
  Posted automatically · {today_str} · goforsports.net
</p>

</div>"""


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Daily football news blog poster")
    parser.add_argument("--config", default=CONFIG_FILE, help="Path to master_config.json")
    parser.add_argument("--dry-run", action="store_true", help="Generate content but do NOT post to Blogger")
    parser.add_argument("--force", action="store_true", help="Post even if already posted today")
    parser.add_argument("--gemini-key", default="", help="Gemini API key (overrides config)")
    parser.add_argument("--openai-key", default="", help="OpenAI API key (overrides config)")
    parser.add_argument("--imgbb-key", default="", help="imgbb API key (overrides config)")
    args = parser.parse_args()

    log("=" * 60)
    log("Daily Football Blog Poster — starting")

    config = load_config(args.config)
    cfg = get_daily_blog_config(config)
    state = load_state()
    today_str = datetime.now(IST).strftime("%B %d, %Y")
    today_key = datetime.now(IST).strftime("%Y-%m-%d")
    thumb_path = f"data/daily_blog_thumb_{today_key}.jpg"

    # ── Dedup check ──
    if not args.force and already_posted_today(state):
        log(f"Already posted today ({today_key}). Use --force to override.")
        return

    # ── Gather news ──
    news_items = gather_top_football_news(max_total=8)
    if not news_items:
        log("No news items found from RSS feeds — aborting.", "ERROR")
        sys.exit(1)

    # ── CLI key overrides ──
    if args.gemini_key:
        cfg["gemini_api_key"] = args.gemini_key
    if args.openai_key:
        cfg["openai_api_key"] = args.openai_key
    if args.imgbb_key:
        cfg["imgbb_api_key"] = args.imgbb_key

    # ── Generate content — try Gemini → OpenAI → RSS fallback ──
    gemini_key = cfg.get("gemini_api_key", "")
    openai_key = cfg.get("openai_api_key", "")
    title, body_html = "", ""

    if gemini_key:
        log("Generating content with Gemini AI (free tier)...")
        try:
            title, body_html = generate_blog_content_gemini(gemini_key, news_items, cfg)
            log("Gemini content generation successful")
        except Exception as e:
            log(f"Gemini generation failed: {e} — trying OpenAI", "WARN")

    if not title and openai_key:
        log("Generating content with OpenAI...")
        try:
            title, body_html = generate_blog_content_openai(openai_key, news_items, cfg)
            log("OpenAI content generation successful")
        except Exception as e:
            log(f"OpenAI generation failed: {e} — using RSS fallback", "WARN")

    if not title:
        log("No AI key configured or all AI attempts failed — using RSS-based content")
        title, body_html = fallback_blog_content(news_items)

    log(f"Post title: {title}")

    # ── Generate thumbnail ──
    generate_blog_thumbnail(title, today_str, thumb_path)

    # ── Host image ──
    thumbnail_url = ""
    imgbb_key = cfg.get("imgbb_api_key", "")
    if imgbb_key and os.path.exists(thumb_path):
        thumbnail_url = upload_to_imgbb(thumb_path, imgbb_key)

    if not thumbnail_url and os.path.exists(thumb_path):
        log("imgbb not configured — embedding thumbnail as data URI")
        thumbnail_url = image_to_data_uri(thumb_path)

    # ── Assemble HTML ──
    labels = cfg.get("post_labels", ["Football", "Football News"])
    post_html = build_post_html(title, body_html, thumbnail_url, news_items, today_str)

    if args.dry_run:
        log("DRY RUN — not posting to Blogger")
        preview_path = f"data/daily_blog_preview_{today_key}.html"
        os.makedirs("data", exist_ok=True)
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(f"<html><head><title>{title}</title></head><body>{post_html}</body></html>")
        log(f"Preview saved: {preview_path}")
        return

    # ── Auth & Post ──
    if not cfg.get("refresh_token"):
        log("No refresh_token configured — run auth_blogger.py first", "ERROR")
        sys.exit(1)

    log("Getting OAuth access token...")
    access_token = get_access_token(cfg)

    log("Posting to Blogger...")
    post_id, post_url = create_blogger_post(cfg, access_token, title, post_html, labels)
    log(f"Post published! ID={post_id} URL={post_url}")

    # ── Update state ──
    state["last_post_date"] = today_key
    posted = state.get("posted_titles", [])
    posted.insert(0, {"title": title, "url": post_url, "date": today_key})
    state["posted_titles"] = posted[:30]  # keep last 30
    save_state(state)
    log("State updated. Done.")
    log("=" * 60)


if __name__ == "__main__":
    main()
