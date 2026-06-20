#!/usr/bin/env python3
import os
import re
import sys
import json
import argparse
import time
import asyncio
import base64
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse, parse_qs
from html.parser import HTMLParser
from html import escape as html_escape

from automation_config import get_player_blog_config, load_automation_config
from pipeline_storage import ensure_runtime_dirs, load_schedule, storage_config

# ----------------------------------------------------------------------
# Domain Health Tracking
# ----------------------------------------------------------------------
DOMAIN_HEALTH_FILE = os.path.join("data", "domain_health.json")

def load_domain_health():
    """Load domain health stats from disk."""
    if os.path.exists(DOMAIN_HEALTH_FILE):
        try:
            with open(DOMAIN_HEALTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_domain_health(health):
    """Persist domain health stats to disk."""
    try:
        os.makedirs(os.path.dirname(DOMAIN_HEALTH_FILE), exist_ok=True)
        with open(DOMAIN_HEALTH_FILE, "w", encoding="utf-8") as f:
            json.dump(health, f, indent=2)
    except Exception as e:
        print(f"[-] Warning: Failed to save domain health: {e}", file=sys.stderr)

def record_domain_result(health, url, success):
    """Record a success or failure for a domain."""
    domain = urlparse(url).netloc
    if not domain:
        return
    entry = health.setdefault(domain, {"fail_count": 0, "success_count": 0})
    if success:
        entry["success_count"] = entry.get("success_count", 0) + 1
    else:
        entry["fail_count"] = entry.get("fail_count", 0) + 1

def sort_urls_by_domain_health(urls, health):
    """Sort URLs so healthy domains come first, failing domains last."""
    def health_score(url):
        domain = urlparse(url).netloc
        entry = health.get(domain, {})
        return entry.get("fail_count", 0) - entry.get("success_count", 0)
    return sorted(urls, key=health_score)

# ----------------------------------------------------------------------
# Crawl4AI Browser-Based Fetcher
# ----------------------------------------------------------------------
_CRAWL4AI_AVAILABLE = None

def is_crawl4ai_available():
    """Check if Crawl4AI is available (cached)."""
    global _CRAWL4AI_AVAILABLE
    if _CRAWL4AI_AVAILABLE is None:
        try:
            from crawl4ai import AsyncWebCrawler
            _CRAWL4AI_AVAILABLE = True
        except ImportError:
            _CRAWL4AI_AVAILABLE = False
            print("[!] Crawl4AI not available. Using requests-only mode.", file=sys.stderr)
    return _CRAWL4AI_AVAILABLE

async def fetch_page_with_browser(url, timeout_ms=25000):
    """Fetch a page using Crawl4AI (Playwright) with full JS execution.
    Returns (html_content, success) tuple.
    """
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

    browser_config = BrowserConfig(
        headless=True,
        text_mode=False,
        extra_args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
    )
    run_config = CrawlerRunConfig(
        wait_until="networkidle",
        page_timeout=timeout_ms,
        js_code=[
            "window.scrollTo(0, document.body.scrollHeight);",
            "await new Promise(r => setTimeout(r, 1500));",
            "window.scrollTo(0, 0);",
        ],
    )
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
            if result.success and result.html:
                return result.html, True
            return None, False
    except Exception as e:
        print(f"[-] Crawl4AI fetch failed for {url}: {e}", file=sys.stderr)
        return None, False

def fetch_page_with_browser_sync(url, timeout_ms=25000):
    """Synchronous wrapper for the async browser fetch."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                html, success = pool.submit(
                    lambda: asyncio.run(fetch_page_with_browser(url, timeout_ms))
                ).result(timeout=timeout_ms // 1000 + 10)
            return html, success
        else:
            return loop.run_until_complete(fetch_page_with_browser(url, timeout_ms))
    except Exception:
        return asyncio.run(fetch_page_with_browser(url, timeout_ms))

def fetch_page_html(url, headers=None, use_browser=False, timeout=15):
    """Unified page fetcher: tries browser (Crawl4AI) first if enabled, falls back to requests.
    Returns (html_content, success, method_used) tuple.
    """
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    # Try browser-based fetch for JS-heavy pages
    if use_browser and is_crawl4ai_available():
        html, success = fetch_page_with_browser_sync(url, timeout_ms=timeout * 1000)
        if success and html:
            print(f"[+] Browser fetch succeeded for {url} ({len(html)} chars)")
            return html, True, "crawl4ai"
        print(f"[-] Browser fetch failed for {url}, falling back to requests")

    # Standard requests fallback
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.text, True, "requests"
    except Exception as e:
        print(f"[-] requests fetch failed for {url}: {e}", file=sys.stderr)
        return None, False, "requests"

# List of domains known to require JS rendering
JS_HEAVY_DOMAINS = {
    "football.scoopnonstop.com",
    "sportstrack.yallatvlive.com",
    "sportstrack.me",
    "90live.yallatvlive.com",
    "vivo.epicsportss.com",
    "fifawcbycxf.pages.dev",
    "cxfoot.pages.dev",
}

def should_use_browser(url):
    """Determine if a URL should be fetched with the browser (Playwright)."""
    domain = urlparse(url).netloc.lower()
    return domain in JS_HEAVY_DOMAINS

# ----------------------------------------------------------------------
# Junk Iframe Filtering — blocks ads, tracking, self-embeds, wrong-match pages
# ----------------------------------------------------------------------
JUNK_IFRAME_DOMAIN_PATTERNS = {
    # Ad networks / tracking
    "criteo.com", "doubleclick.net", "googlesyndication.com",
    "googletagmanager.com", "googleadservices.com", "google-analytics.com",
    "facebook.com", "facebook.net", "twitter.com", "instagram.com",
    "amazon-adsystem.com", "adnxs.com", "outbrain.com", "taboola.com",
    "adsafeprotected.com", "moatads.com", "highperformanceformat.com",
    "effectivecpmnetwork.com", "profitableratecpm.com",
    # Captcha / bot protection
    "google.com/recaptcha", "hcaptcha.com", "cloudflare.com/cdn-cgi",
    # Analytics
    "hotjar.com", "clarity.ms", "chartbeat.com",
    # Social widgets
    "disqus.com", "addthis.com", "sharethis.com",
    # Known tracker/redirect domains
    "arizonaplay.club",
    # Image hosting / static assets domains (to prevent embedding photos)
    "bp.blogspot.com", "googleusercontent.com", "ggpht.com",
    "cloudinary.com", "imgur.com", "wp.com", "gravatar.com",
    "postimg.cc", "postimages.org", "imgbb.com", "imagebam.com",
    "photobucket.com", "flickr.com", "mediafire.com",
}

JUNK_IFRAME_PATH_PATTERNS = {
    # Blogspot template/utility pages
    "/p/base-button", "/p/base-link", "/p/btn-", "/p/button-",
    # Generic non-stream paths
    "/ads", "/ad-", "/pixel", "/beacon", "/tracking", "/analytics",
    "/ns.html",  # GTM noscript
    "/syncframe",  # Criteo sync
    "/api2/aframe",  # reCAPTCHA
}

def is_junk_iframe(url, source_urls=None):
    """Check if an iframe URL is junk (ads, tracking, self-embeds, wrong pages).
    
    Args:
        url: The iframe URL to check
        source_urls: Optional set of source portal URLs being scraped
                     (to prevent embedding the source site itself)
    Returns:
        True if the URL should be rejected as a junk iframe
    """
    if not url or not url.startswith("http"):
        return True
    
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path_lower = parsed.path.lower()
    url_lower = url.lower()
    
    # Reject ignored extensions (images, css, scripts, fonts, documents, etc.)
    ignored_extensions = {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
        ".css", ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z", ".tar", ".gz",
        ".js", ".json"
    }
    if any(path_lower.endswith(ext) for ext in ignored_extensions):
        return True

    # Reject root/index paths or empty paths (homepages)
    path_clean = path_lower.strip("/")
    if not path_clean or path_clean in ("index.html", "index.php", "home.html", "m=1"):
        if not parsed.query:
            return True

    # Reject homepages / root paths / non-player pages of known portal domains
    portal_domains = {
        "epicsports.in", "epicsports.blog", "footem.co.in", "90live.in",
        "yallatvlive.com", "notebookpot.com", "sportstrack.me", "soccervent.xyz",
        "epicsportss.com", "scoopnonstop.com", "blogspot.com", "pages.dev",
        "gamesaved.xyz"
    }
    is_portal_domain = any(domain == pd or domain.endswith("." + pd) for pd in portal_domains)
    if is_portal_domain:
        # Only allow pages that look like actual embed players, not raw website pages
        # Require strong embed indicators in the path - "live" alone is too generic
        embed_keywords = ["embed", "player", "ch=", "albaplayer"]
        has_embed_path = any(kw in path_lower for kw in embed_keywords)
        # Weak keywords that need additional context (not just /p/some-live-page.html)
        weak_keywords = ["stream", "watch", "play"]
        has_weak_path = any(kw in path_lower for kw in weak_keywords)
        # /p/ pages on blogspot/portals are almost always content pages, not embeddable players
        is_blogspot_page = "/p/" in path_lower
        if not has_embed_path:
            if is_blogspot_page or not has_weak_path:
                return True

    # Reject raw website URLs that look like content/article pages (not embeddable players)
    raw_website_indicators = [
        "scroll-down", "enjoy-live-match", "match-preview", "live-score",
        "lineup", "telecast", "preview", "schedule", "highlights",
        "how-to-watch", "where-to-watch", "kick-off",
    ]
    if any(ind in path_lower for ind in raw_website_indicators):
        return True
    
    # Check domain blocklist
    for pattern in JUNK_IFRAME_DOMAIN_PATTERNS:
        if "/" in pattern:
            # Pattern includes path (e.g. google.com/recaptcha)
            if pattern in url_lower:
                return True
        else:
            # Domain-only pattern
            if domain == pattern or domain.endswith("." + pattern):
                return True
    
    # Check path blocklist
    for pattern in JUNK_IFRAME_PATH_PATTERNS:
        if pattern in path_lower:
            return True
    
    # Reject blogspot utility/template pages (not match-specific content)
    if "blogspot.com/p/" in url_lower and not any(
        kw in path_lower for kw in ["/p/live", "/p/stream", "/p/watch", "/p/player"]
    ):
        return True
    
    # Reject source portal URLs being embedded back as iframes
    # (this is the "showing website inside player" bug)
    if source_urls:
        iframe_canon = domain + path_lower.rstrip("/")
        for src_url in source_urls:
            src_parsed = urlparse(src_url)
            src_canon = src_parsed.netloc.lower() + src_parsed.path.lower().rstrip("/")
            # Same domain + same/similar path = self-embed
            if iframe_canon == src_canon:
                return True
            # Same domain, different match page = wrong match embed
            if domain == src_parsed.netloc.lower() and path_lower.rstrip("/") != src_parsed.path.lower().rstrip("/"):
                # Only block if it looks like a match page (has a date-like path)
                if re.search(r'/\d{4}/\d{2}/', path_lower):
                    return True
    
    return False

# ----------------------------------------------------------------------
# HTML Link Parser
# ----------------------------------------------------------------------
class EpicLinkParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.results = []
        self.current_tag = None
        self.current_attrs = {}
        self.current_text = []
        self.open_tags = []  # Stack of tuples: (tag_name, is_ignored)

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        is_ignored_container = False
        if tag in ("aside", "header", "footer", "nav"):
            is_ignored_container = True
        else:
            for attr_name in ("class", "id"):
                if attr_name in attr_dict:
                    val = attr_dict[attr_name].lower()
                    if any(kw in val for kw in ("sidebar", "widget", "related", "popular", "menu", "navbar", "comment", "footer", "header")):
                        if not ("blog" in val or "post" in val):
                            is_ignored_container = True
                            break
        
        self.open_tags.append((tag, is_ignored_container))
            
        if tag in ("a", "button", "iframe"):
            self.current_tag = tag
            self.current_attrs = attr_dict
            self.current_text = []

    def handle_data(self, data):
        if self.current_tag:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        # Determine if we are currently inside an ignored container BEFORE popping
        is_in_ignored = any(is_ignored for _, is_ignored in self.open_tags)
        
        # Pop from open_tags stack
        while self.open_tags:
            popped_tag, _ = self.open_tags.pop()
            if popped_tag == tag:
                break
                
        if tag == self.current_tag:
            text = "".join(self.current_text).strip()
            url = None
            if "href" in self.current_attrs:
                url = self.current_attrs["href"]
            elif "src" in self.current_attrs and tag == "iframe":
                url = self.current_attrs["src"]
            elif "onclick" in self.current_attrs:
                onclick_val = self.current_attrs["onclick"]
                match = re.search(r"'(https?://[^'\s]+)'|\"(https?://[^\"]+)\"", onclick_val)
                if match:
                    url = match.group(1) or match.group(2)
            
            if url and not is_in_ignored:
                resolved_url = urljoin(self.base_url, url)
                self.results.append({
                    "text": text.replace("\n", " ").strip(),
                    "url": resolved_url,
                    "tag": tag
                })
            
            self.current_tag = None
            self.current_attrs = {}
            self.current_text = []

# ----------------------------------------------------------------------
# HTML Player Template with Shaka Player DRM Support Integrated
# ----------------------------------------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1">
<title>Live Player</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="preconnect" href="https://cdnjs.cloudflare.com" crossorigin>
<link rel="preconnect" href="https://throughalivemedication.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=Inter:wght@400;500&display=swap" rel="stylesheet">

<!-- Shaka Player (DASH + HLS native) -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.7.11/shaka-player.compiled.min.js"></script>
<!-- HLS.js fallback -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.4.10/hls.min.js"></script>

<!-- ======= AD HEAD CODE ======= -->
##PLAYER_HEAD_AD_CODE##

<style>
:root {
  --red: #e63946;
  --dark: #0a0a0f;
  --card: #111118;
  --border: rgba(255,255,255,0.07);
  --text: #f0f0f0;
  --muted: #666;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background: var(--dark);
  color: var(--text);
  font-family: 'Inter', sans-serif;
  min-height: 100vh;
}
.site-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: #0d0d14;
  border-bottom: 1px solid var(--border);
}
.logo {
  font-family: 'Rajdhani', sans-serif;
  font-size: 22px;
  font-weight: 700;
  color: #fff;
  text-decoration: none;
  letter-spacing: 1px;
}
.logo span { color: var(--red); }
.header-right { display: flex; align-items: center; gap: 10px; }
.live-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(230,57,70,0.12);
  border: 1px solid rgba(230,57,70,0.3);
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
  color: var(--red);
  letter-spacing: 1px;
}
.live-badge .dot {
  width: 7px; height: 7px;
  background: var(--red);
  border-radius: 50%;
  animation: blink 1.2s infinite;
}
/* Engine badge */
.engine-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 20px;
  letter-spacing: 0.6px;
  font-family: 'Rajdhani', sans-serif;
  transition: all 0.3s;
}
.engine-badge.dash  { background: rgba(52,152,219,0.15); color: #3498db; border: 1px solid rgba(52,152,219,0.3); }
.engine-badge.hls   { background: rgba(46,204,113,0.15); color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }
.engine-badge.mp4   { background: rgba(155,89,182,0.15); color: #9b59b6; border: 1px solid rgba(155,89,182,0.3); }
.engine-badge.iframe{ background: rgba(241,196,15,0.15);  color: #f1c40f;  border: 1px solid rgba(241,196,15,0.3); }
.engine-badge.none  { background: rgba(255,255,255,0.05); color: #666;    border: 1px solid rgba(255,255,255,0.1); }

@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.2} }
.alert-bar {
  background: linear-gradient(90deg, #1a0a0a, #1f0d0d, #1a0a0a);
  border-bottom: 1px solid rgba(230,57,70,0.2);
  padding: 8px 16px;
  text-align: center;
  font-size: 12.5px;
  color: #ccc;
}
.alert-bar strong { color: var(--red); }
.alert-bar a { color: #f4c430; text-decoration: none; font-weight: 600; margin: 0 4px; }
.ad-top { text-align:center; padding: 6px 0; background:#0d0d14; }
.main { max-width: 960px; margin: 0 auto; padding: 16px 12px; }

/* ── Player card ── */
.player-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.video-wrap {
  position: relative;
  width: 100%;
  aspect-ratio: 16/9;
  background: #000;
}
video { width:100%; height:100%; display:block; background:#000; }
/* hide native video in iframe mode to prevent gap behind iframe */
.video-wrap.iframe-mode video { display: none; }

/* iframe mode */
.iframe-wrap {
  position: absolute;
  inset: 0;
  display: none;
  z-index: 5;
  overflow: hidden;
}
.iframe-wrap iframe {
  width: 100%;
  height: 100%;
  border: none;
  display: block;
  overflow: hidden;
}
.iframe-wrap.active { display: block; }

/* controls */
.controls-bar {
  position: absolute;
  bottom: 0; left: 0; right: 0;
  background: linear-gradient(transparent, rgba(0,0,0,0.85));
  padding: 30px 14px 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  opacity: 0;
  transition: opacity 0.25s;
  z-index: 10;
}
.video-wrap:hover .controls-bar,
.video-wrap.show-controls .controls-bar { opacity: 1; }
/* hide controls in iframe mode */
.video-wrap.iframe-mode .controls-bar { display: none; }

.ctrl-btn {
  background: none;
  border: none;
  color: #fff;
  cursor: pointer;
  padding: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: background 0.15s;
}
.ctrl-btn:hover { background: rgba(255,255,255,0.12); }
.ctrl-btn svg { width:20px; height:20px; fill:currentColor; }
.progress-wrap {
  flex: 1;
  height: 4px;
  background: rgba(255,255,255,0.2);
  border-radius: 2px;
  cursor: pointer;
}
.progress-bar {
  height: 100%;
  background: var(--red);
  border-radius: 2px;
  width: 0%;
  transition: width 0.5s linear;
}
.vol-wrap { display: flex; align-items: center; gap: 6px; }
input[type=range].vol-slider {
  width: 60px; height: 3px;
  accent-color: var(--red);
  cursor: pointer;
}
.quality-select {
  background: rgba(255,255,255,0.1);
  border: 1px solid rgba(255,255,255,0.15);
  color: #fff;
  font-size: 11px;
  padding: 3px 6px;
  border-radius: 4px;
  cursor: pointer;
  outline: none;
}
.quality-select option { background: #111; color: #fff; }
.time-label {
  font-size: 11px;
  color: rgba(255,255,255,0.7);
  font-family: 'Rajdhani', sans-serif;
  letter-spacing: 0.5px;
  white-space: nowrap;
}

/* overlays */
.overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  z-index: 20;
  background: rgba(0,0,0,0.85);
  text-align: center;
  padding: 20px;
}
.overlay.hidden { display: none; }
.spinner {
  width: 48px; height: 48px;
  border: 3px solid rgba(230,57,70,0.2);
  border-top-color: var(--red);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.overlay p { font-size: 13px; color: #aaa; }
.overlay .sub { font-size: 11px; color: #555; margin-top: -8px; }
.error-icon { font-size: 40px; }
.error-title { font-size: 17px; font-weight: 600; color: var(--red); font-family: 'Rajdhani', sans-serif; }
.retry-btn {
  margin-top: 4px;
  padding: 8px 24px;
  background: var(--red);
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  font-family: 'Rajdhani', sans-serif;
  letter-spacing: 0.5px;
  transition: opacity 0.2s;
}
.retry-btn:hover { opacity: 0.85; }
.engine-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: -4px;
}
.engine-try {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 4px;
  font-family: 'Rajdhani', sans-serif;
  font-weight: 600;
  letter-spacing: 0.4px;
  background: rgba(255,255,255,0.05);
  color: #555;
  border: 1px solid rgba(255,255,255,0.08);
  transition: all 0.3s;
}
.engine-try.trying  { color: #f39c12; border-color: rgba(243,156,18,0.4); background: rgba(243,156,18,0.08); }
.engine-try.success { color: #2ecc71; border-color: rgba(46,204,113,0.4); background: rgba(46,204,113,0.08); }
.engine-try.failed  { color: #555;    border-color: rgba(255,255,255,0.06); text-decoration: line-through; }

.player-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
  gap: 8px;
}
.stream-status {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  color: #aaa;
}
.stream-status .sdot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: #555;
  flex-shrink: 0;
}
.stream-status.live   .sdot { background: #2ecc71; animation: blink 1.2s infinite; }
.stream-status.error  .sdot { background: var(--red); }
.stream-status.buffer .sdot { background: #f39c12; animation: blink 0.6s infinite; }
.notice { font-size: 12px; color: #f39c12; }

/* ── STREAM LINKS ── */
.stream-links-section {
  margin-top: 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.stream-links-header {
  padding: 10px 14px;
  font-family: 'Rajdhani', sans-serif;
  font-size: 13px;
  font-weight: 600;
  color: #888;
  letter-spacing: 0.8px;
  text-transform: uppercase;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.type-legend {
  display: flex;
  gap: 6px;
}
.stream-links-list { display: flex; flex-direction: column; }
.stream-link-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
  transition: background 0.15s;
  text-decoration: none;
}
.stream-link-item:last-child { border-bottom: none; }
.stream-link-item:hover { background: rgba(255,255,255,0.04); }
.stream-link-item.active { background: rgba(230,57,70,0.08); border-left: 3px solid var(--red); }
.stream-link-item.disabled { opacity: 0.4; pointer-events: none; }
.link-num {
  font-family: 'Rajdhani', sans-serif;
  font-size: 13px;
  font-weight: 700;
  color: var(--red);
  min-width: 22px;
}
.link-info { flex: 1; }
.link-label { font-size: 13px; font-weight: 500; color: var(--text); display: block; }
.link-meta  { font-size: 11px; color: #666; margin-top: 2px; display: block; }
.link-badges { display: flex; gap: 5px; flex-wrap: wrap; }
.badge {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  font-family: 'Rajdhani', sans-serif;
  letter-spacing: 0.3px;
}
.badge.hd    { background: rgba(46,204,113,0.15);  color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }
.badge.sd    { background: rgba(241,196,15,0.15);   color: #f1c40f; border: 1px solid rgba(241,196,15,0.3); }
.badge.eng   { background: rgba(52,152,219,0.15);   color: #3498db; border: 1px solid rgba(52,152,219,0.3); }
.badge.ara   { background: rgba(155,89,182,0.15);   color: #9b59b6; border: 1px solid rgba(155,89,182,0.3); }
.badge.ios   { background: rgba(255, 159, 64, 0.22); color: #ff9f40; border: 1px solid rgba(255, 159, 64, 0.45); font-weight: 700; box-shadow: 0 0 4px rgba(255, 159, 64, 0.2); }
.badge.auto  { background: rgba(230,57,70,0.12);    color: var(--red); border: 1px solid rgba(230,57,70,0.25); }
.badge.dash  { background: rgba(52,152,219,0.12);   color: #3498db; border: 1px solid rgba(52,152,219,0.3); }
.badge.hls   { background: rgba(46,204,113,0.12);   color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }
.badge.mp4   { background: rgba(155,89,182,0.12);   color: #9b59b6; border: 1px solid rgba(155,89,182,0.3); }
.badge.iframe{ background: rgba(241,196,15,0.12);   color: #f1c40f; border: 1px solid rgba(241,196,15,0.3); }
.link-play-icon { color: #444; transition: color 0.15s; }
.stream-link-item:hover .link-play-icon,
.stream-link-item.active .link-play-icon { color: var(--red); }
.link-play-icon svg { width: 18px; height: 18px; fill: currentColor; }

.ad-mid { text-align:center; margin: 14px 0; }
.socials {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin: 16px 0;
}
.soc-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 14px 20px;
  border-radius: 10px;
  font-size: 15px;
  font-weight: 700;
  text-decoration: none;
  color: #fff;
  font-family: 'Rajdhani', sans-serif;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  transition: all 0.3s ease;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  cursor: pointer;
}
.soc-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0,0,0,0.25);
  opacity: 0.95;
}
.soc-btn.wa {
  background: linear-gradient(135deg, #25D366 0%, #128C7E 100%);
  animation: pulse-green 2s infinite;
}
.soc-btn.tg {
  background: linear-gradient(135deg, #0088cc 0%, #006699 100%);
  animation: pulse-blue 2s infinite;
}
@keyframes pulse-green {
  0% { box-shadow: 0 0 0 0 rgba(37, 211, 102, 0.4); }
  70% { box-shadow: 0 0 0 10px rgba(37, 211, 102, 0); }
  100% { box-shadow: 0 0 0 0 rgba(37, 211, 102, 0); }
}
@keyframes pulse-blue {
  0% { box-shadow: 0 0 0 0 rgba(0, 136, 204, 0.4); }
  70% { box-shadow: 0 0 0 10px rgba(0, 136, 204, 0); }
  100% { box-shadow: 0 0 0 0 rgba(0, 136, 204, 0); }
}
@media(max-width:480px) {
  .socials {
    grid-template-columns: 1fr;
  }
}
.smartlink-wrap {
  text-align: center;
  margin: 14px 0 18px;
}
.smartlink-btn {
  display: inline-block;
  width: 100%;
  max-width: 560px;
  box-sizing: border-box;
  background: #f4c430;
  color: #111;
  text-decoration: none;
  padding: 13px 18px;
  border-radius: 8px;
  border: 1px solid #b98900;
  font-family: 'Rajdhani', sans-serif;
  font-weight: 800;
  letter-spacing: 0.7px;
  text-transform: uppercase;
  animation: smart-pulse 1.8s infinite;
}
.smartlink-btn:hover { transform: translateY(-1px); opacity: 0.95; }
@keyframes smart-pulse {
  0% { box-shadow: 0 0 0 0 rgba(244,196,48,0.55); }
  70% { box-shadow: 0 0 0 10px rgba(244,196,48,0); }
  100% { box-shadow: 0 0 0 0 rgba(244,196,48,0); }
}
.disclaimer {
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
  font-size: 11.5px;
  color: #555;
  line-height: 1.7;
  margin: 14px 0;
}
.ad-bottom { text-align:center; padding: 14px 0; }



@media(max-width:600px) {
  .site-header { padding: 10px 14px; }
  .logo { font-size: 18px; }
  input[type=range].vol-slider { width: 44px; }
  .time-label { display: none; }
  .link-label { font-size: 12px; }
  .type-legend { display: none; }
}
@media (max-width: 768px) {
  .main { padding: 8px 6px; }
  .player-card { border-radius: 8px; }
  .video-wrap { aspect-ratio: 16/9; }
  .socials { gap: 8px; margin: 10px 0; }
  .soc-btn { padding: 12px 16px; font-size: 13px; border-radius: 8px; }
  .stream-link-item { padding: 9px 12px; }
  .link-label { font-size: 12px; }
  .badge { font-size: 9px; padding: 1px 5px; }
}
</style>
</head>
<body>

<header class="site-header">
  <a href="#" class="logo">WORLD<span>CUP</span></a>
  <div class="header-right">
    <span class="engine-badge none" id="engine-badge">DETECTING</span>
    <div class="live-badge"><div class="dot"></div> LIVE</div>
  </div>
</header>

<div class="alert-bar">
  <strong>🛑 ALERT</strong> — If a stream does not start, the next link is tried automatically.
  Join our <a href="#" onclick="goSomewhere(); return false;">WhatsApp Group</a> for daily live links 👇
</div>

<div class="ad-top">##PLAYER_TOP_AD_CODE##</div>

<div class="main">

  <div class="socials">
    <a href="#" onclick="goSomewhere(); return false;" class="soc-btn wa">
      <svg style="width:18px; height:18px; fill:currentColor" viewBox="0 0 24 24">
        <path d="M.057 24l1.687-6.163c-1.041-1.804-1.588-3.849-1.587-5.946C.06 5.348 5.397.01 12.008.01c3.202.001 6.212 1.246 8.477 3.514 2.266 2.268 3.507 5.28 3.505 8.484-.004 6.657-5.34 11.997-11.953 11.997-2.005-.001-3.973-.502-5.724-1.455L0 24zm6.59-4.846c1.66.986 3.298 1.448 5.355 1.449 5.883 0 10.675-4.76 10.677-10.606.002-2.833-1.107-5.498-3.127-7.52-2.02-2.022-4.704-3.136-7.54-3.137-5.887 0-10.683 4.761-10.686 10.61 0 2.235.632 4.04 1.766 5.887l-.999 3.647 3.854-.993zm11.381-4.708c-.307-.154-1.82-.899-2.102-1.002-.282-.102-.487-.154-.692.154-.205.308-.795 1.002-.974 1.205-.18.206-.36.23-.667.077-.307-.154-1.297-.477-2.472-1.528-.915-.817-1.533-1.828-1.713-2.136-.18-.308-.02-.475.134-.628.14-.137.307-.359.461-.54.154-.179.206-.308.308-.513.102-.206.051-.385-.026-.54-.077-.154-.692-1.67-.949-2.285-.25-.602-.503-.519-.692-.53l-.59-.011c-.205 0-.538.077-.82.385-.282.308-1.077 1.051-1.077 2.562 0 1.513 1.102 2.975 1.256 3.18 1.532 2.054 3.393 3.197 5.258 3.829 1.865.63 2.72.76 3.655.62.934-.14 2.102-.859 2.397-1.692.296-.834.296-1.547.207-1.693-.089-.147-.282-.25-.59-.404z"/>
      </svg>
      WhatsApp Group
    </a>
    <a href="#" onclick="telewhere(); return false;" class="soc-btn tg">
      <svg style="width:18px; height:18px; fill:currentColor" viewBox="0 0 24 24">
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69.01-.03.01-.14-.07-.2-.08-.06-.19-.04-.27-.02-.11.02-1.93 1.23-5.46 3.62-.51.35-.98.53-1.4.52-.46-.01-1.35-.26-2.01-.48-.81-.27-1.46-.42-1.4-.88.03-.24.36-.49.98-.74 3.82-1.66 6.37-2.75 7.63-3.27 3.63-1.49 4.38-1.75 4.88-1.76.11 0 .35.03.51.16.13.11.17.26.19.37.02.13.02.26.01.39z"/>
      </svg>
      Telegram Group
    </a>
  </div>

  <div class="player-card">
    <div class="video-wrap" id="vwrap">

      <!-- Native video element (used by DASH / HLS / MP4) -->
      <video id="video" playsinline autoplay muted></video>

      <!-- iframe container (used when URL is an embed) -->
      <div class="iframe-wrap" id="iframe-wrap" style="position: relative;">
        <iframe id="iframe-player"
          allowfullscreen
          allow="autoplay; encrypted-media; picture-in-picture"
          sandbox="allow-scripts allow-same-origin allow-presentation"
          referrerpolicy="no-referrer"
          scrolling="no"></iframe>
        <div id="iframe-click-overlay" style="position: absolute; inset: 0; z-index: 8; cursor: pointer; background: transparent;"></div>
      </div>

      <!-- Loading overlay -->
      <div class="overlay" id="ov-load">
        <div class="spinner"></div>
        <p id="ov-load-msg">Detecting stream type...</p>
        <div class="engine-list" id="engine-list"></div>
      </div>

      <!-- Error overlay -->
      <div class="overlay hidden" id="ov-err">
        <div class="error-icon">⚠️</div>
        <div class="error-title">Stream Error</div>
        <p id="err-msg">Could not load the stream.</p>
        <button class="retry-btn" id="retry-btn">▶ Try Next Link</button>
      </div>

      <!-- No URL overlay -->
      <div class="overlay hidden" id="ov-none">
        <div class="error-icon">📺</div>
        <div class="error-title">No Stream Link</div>
        <p>Pass a stream URL via <code style="color:#f4c430">?url=</code> or click a link below.</p>
        <p style="font-size:11px; color:#555; margin-top:4px;">Supports .mpd · .m3u8 · .mp4/.webm · iframe embeds</p>
      </div>

      <!-- Controls (hidden in iframe mode) -->
      <div class="controls-bar" id="cbar">
        <button class="ctrl-btn" id="btn-play" title="Play/Pause">
          <svg id="ico-play"  viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
          <svg id="ico-pause" viewBox="0 0 24 24" style="display:none"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
        </button>

        <div class="progress-wrap">
          <div class="progress-bar" id="prog-bar"></div>
        </div>

        <span class="time-label" id="time-lbl">● LIVE</span>

        <div class="vol-wrap">
          <button class="ctrl-btn" id="btn-mute" title="Unmute">
            <svg id="ico-vol"  viewBox="0 0 24 24" style="display:none"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0014 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>
            <svg id="ico-mute" viewBox="0 0 24 24"><path d="M16.5 12A4.5 4.5 0 0014 7.97v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>
          </button>
          <input type="range" class="vol-slider" id="vol-slider" min="0" max="1" step="0.05" value="0">
        </div>

        <select class="quality-select" id="quality-sel" title="Quality">
          <option value="-1">Auto</option>
        </select>

        <button class="ctrl-btn" id="btn-fs" title="Fullscreen">
          <svg id="ico-fs" viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
          <svg id="ico-ex" viewBox="0 0 24 24" style="display:none"><path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>
        </button>
      </div>
    </div><!-- /video-wrap -->

    <div class="player-info">
      <div class="stream-status" id="sstatus">
        <div class="sdot"></div>
        <span id="stext">Initializing player...</span>
      </div>
      <div class="notice">The player automatically skips links that do not start.</div>
    </div>
  </div><!-- /player-card -->

  ##PLAYER_SMARTLINK_BUTTON##

  <!-- ══ STREAM LINKS ── -->
  <div class="stream-links-section" id="links-section">
    <div class="stream-links-header">
      <span>📡 Available Streams</span>
      <div class="type-legend">
        <span class="badge dash">DASH</span>
        <span class="badge hls">HLS</span>
        <span class="badge mp4">MP4</span>
        <span class="badge iframe">EMBED</span>
      </div>
    </div>
    <div class="stream-links-list" id="links-list"></div>
  </div>

  <!-- Player mid-page ad and social popup trigger -->
  <div class="ad-mid">
    ##PLAYER_MID_AD_CODE##
    <script>
      window.addEventListener('load', function() {
        // Social Modal Popup Logic
        const today = new Date().toDateString();
        if (localStorage.getItem('seenSocialJoinPopup') !== today) {
          setTimeout(function() {
            const modal = document.getElementById('social-modal-overlay');
            if (modal) modal.style.display = 'flex';
          }, ##PLAYER_SOCIAL_POPUP_DELAY_MS##);
        }

        const closeBtn = document.getElementById('close-social-modal');
        const closeTextBtn = document.getElementById('close-social-modal-btn');
        const modal = document.getElementById('social-modal-overlay');
        
        const closeModal = () => {
          if (modal) {
            modal.style.display = 'none';
            localStorage.setItem('seenSocialJoinPopup', today);
          }
        };

        if (closeBtn) closeBtn.addEventListener('click', closeModal);
        if (closeTextBtn) closeTextBtn.addEventListener('click', closeModal);
      });
    </script>
  </div>

  <div class="socials">
    <a href="#" onclick="goSomewhere(); return false;" class="soc-btn wa">
      <svg style="width:18px; height:18px; fill:currentColor" viewBox="0 0 24 24">
        <path d="M.057 24l1.687-6.163c-1.041-1.804-1.588-3.849-1.587-5.946C.06 5.348 5.397.01 12.008.01c3.202.001 6.212 1.246 8.477 3.514 2.266 2.268 3.507 5.28 3.505 8.484-.004 6.657-5.34 11.997-11.953 11.997-2.005-.001-3.973-.502-5.724-1.455L0 24zm6.59-4.846c1.66.986 3.298 1.448 5.355 1.449 5.883 0 10.675-4.76 10.677-10.606.002-2.833-1.107-5.498-3.127-7.52-2.02-2.022-4.704-3.136-7.54-3.137-5.887 0-10.683 4.761-10.686 10.61 0 2.235.632 4.04 1.766 5.887l-.999 3.647 3.854-.993zm11.381-4.708c-.307-.154-1.82-.899-2.102-1.002-.282-.102-.487-.154-.692.154-.205.308-.795 1.002-.974 1.205-.18.206-.36.23-.667.077-.307-.154-1.297-.477-2.472-1.528-.915-.817-1.533-1.828-1.713-2.136-.18-.308-.02-.475.134-.628.14-.137.307-.359.461-.54.154-.179.206-.308.308-.513.102-.206.051-.385-.026-.54-.077-.154-.692-1.67-.949-2.285-.25-.602-.503-.519-.692-.53l-.59-.011c-.205 0-.538.077-.82.385-.282.308-1.077 1.051-1.077 2.562 0 1.513 1.102 2.975 1.256 3.18 1.532 2.054 3.393 3.197 5.258 3.829 1.865.63 2.72.76 3.655.62.934-.14 2.102-.859 2.397-1.692.296-.834.296-1.547.207-1.693-.089-.147-.282-.25-.59-.404z"/>
      </svg>
      WhatsApp Group
    </a>
    <a href="#" onclick="telewhere(); return false;" class="soc-btn tg">
      <svg style="width:18px; height:18px; fill:currentColor" viewBox="0 0 24 24">
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69.01-.03.01-.14-.07-.2-.08-.06-.19-.04-.27-.02-.11.02-1.93 1.23-5.46 3.62-.51.35-.98.53-1.4.52-.46-.01-1.35-.26-2.01-.48-.81-.27-1.46-.42-1.4-.88.03-.24.36-.49.98-.74 3.82-1.66 6.37-2.75 7.63-3.27 3.63-1.49 4.38-1.75 4.88-1.76.11 0 .35.03.51.16.13.11.17.26.19.37.02.13.02.26.01.39z"/>
      </svg>
      Telegram Group
    </a>
  </div>

  <div class="disclaimer">
    This site does not host any media files. All streams are sourced from third-party external services.
    We are not responsible for externally hosted content. All trademarks, videos, and logos belong to their respective owners.
  </div>

  <div class="ad-bottom">
    ##PLAYER_BOTTOM_AD_CODE##
  </div>

  <!-- Telegram & WhatsApp Social Join Modal -->
  <div id="social-modal-overlay" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.85); z-index:100000; justify-content:center; align-items:center; backdrop-filter: blur(4px); transition: all 0.3s ease;">
    <div style="position:relative; background:#111118; border: 1px solid rgba(255,255,255,0.1); padding:24px; border-radius:16px; width:90%; max-width:440px; box-shadow:0 10px 30px rgba(0,0,0,0.5); text-align:center; animation: popIn 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);">
      <button id="close-social-modal" style="position:absolute; top:12px; right:12px; background:rgba(255,255,255,0.06); color:#fff; border:none; border-radius:50%; width:28px; height:28px; font-size:16px; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:background 0.2s;">&times;</button>
      <h3 style="font-family:'Rajdhani',sans-serif; font-size:20px; font-weight:700; color:#fff; text-transform:uppercase; letter-spacing:1px; margin-bottom:8px;">📢 Live Match Channels</h3>
      <p style="font-size:13px; color:#aaa; line-height:1.5; margin-bottom:20px;">Join our communities to get instant live streaming links and match notifications daily!</p>
      
      <div style="display:flex; flex-direction:column; gap:12px;">
        <a href="#" onclick="goSomewhere(); document.getElementById('social-modal-overlay').style.display='none'; return false;" style="display:flex; align-items:center; justify-content:center; gap:10px; padding:14px 20px; border-radius:10px; font-size:15px; font-weight:700; text-decoration:none; color:#fff; font-family:'Rajdhani',sans-serif; text-transform:uppercase; letter-spacing:0.6px; background:linear-gradient(135deg, #25D366 0%, #128C7E 100%); transition:transform 0.2s, box-shadow 0.2s; box-shadow:0 4px 15px rgba(37,211,102,0.3);">
          <svg style="width:18px; height:18px; fill:currentColor" viewBox="0 0 24 24">
            <path d="M.057 24l1.687-6.163c-1.041-1.804-1.588-3.849-1.587-5.946C.06 5.348 5.397.01 12.008.01c3.202.001 6.212 1.246 8.477 3.514 2.266 2.268 3.507 5.28 3.505 8.484-.004 6.657-5.34 11.997-11.953 11.997-2.005-.001-3.973-.502-5.724-1.455L0 24zm6.59-4.846c1.66.986 3.298 1.448 5.355 1.449 5.883 0 10.675-4.76 10.677-10.606.002-2.833-1.107-5.498-3.127-7.52-2.02-2.022-4.704-3.136-7.54-3.137-5.887 0-10.683 4.761-10.686 10.61 0 2.235.632 4.04 1.766 5.887l-.999 3.647 3.854-.993zm11.381-4.708c-.307-.154-1.82-.899-2.102-1.002-.282-.102-.487-.154-.692.154-.205.308-.795 1.002-.974 1.205-.18.206-.36.23-.667.077-.307-.154-1.297-.477-2.472-1.528-.915-.817-1.533-1.828-1.713-2.136-.18-.308-.02-.475.134-.628.14-.137.307-.359.461-.54.154-.179.206-.308.308-.513.102-.206.051-.385-.026-.54-.077-.154-.692-1.67-.949-2.285-.25-.602-.503-.519-.692-.53l-.59-.011c-.205 0-.538.077-.82.385-.282.308-1.077 1.051-1.077 2.562 0 1.513 1.102 2.975 1.256 3.18 1.532 2.054 3.393 3.197 5.258 3.829 1.865.63 2.72.76 3.655.62.934-.14 2.102-.859 2.397-1.692.296-.834.296-1.547.207-1.693-.089-.147-.282-.25-.59-.404z"/>
          </svg>
          Join WhatsApp Group
        </a>
        <a href="#" onclick="telewhere(); document.getElementById('social-modal-overlay').style.display='none'; return false;" style="display:flex; align-items:center; justify-content:center; gap:10px; padding:14px 20px; border-radius:10px; font-size:15px; font-weight:700; text-decoration:none; color:#fff; font-family:'Rajdhani',sans-serif; text-transform:uppercase; letter-spacing:0.6px; background:linear-gradient(135deg, #0088cc 0%, #006699 100%); transition:transform 0.2s, box-shadow 0.2s; box-shadow:0 4px 15px rgba(0,136,204,0.3);">
          <svg style="width:18px; height:18px; fill:currentColor" viewBox="0 0 24 24">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69.01-.03.01-.14-.07-.2-.08-.06-.19-.04-.27-.02-.11.02-1.93 1.23-5.46 3.62-.51.35-.98.53-1.4.52-.46-.01-1.35-.26-2.01-.48-.81-.27-1.46-.42-1.4-.88.03-.24.36-.49.98-.74 3.82-1.66 6.37-2.75 7.63-3.27 3.63-1.49 4.38-1.75 4.88-1.76.11 0 .35.03.51.16.13.11.17.26.19.37.02.13.02.26.01.39z"/>
          </svg>
          Join Telegram Group
        </a>
      </div>
      <button id="close-social-modal-btn" style="margin-top:16px; background:none; border:none; color:#555; font-size:11px; cursor:pointer; text-decoration:underline;">No, thanks, close this</button>
    </div>
  </div>
  <style>
    @keyframes popIn {
      from { transform: scale(0.9); opacity: 0; }
      to { transform: scale(1); opacity: 1; }
    }
    #close-social-modal:hover { background: rgba(255,255,255,0.15) !important; }
  </style>

</div><!-- /main -->

<script>
// Force fresh load from server if cb parameter is missing or older than 5 minutes
(function() {
  const params = new URLSearchParams(window.location.search);
  const cb = params.get('cb');
  const now = Math.floor(Date.now() / 1000);
  if (!cb || (now - parseInt(cb, 10)) > 300) {
    params.set('cb', now);
    window.location.search = params.toString();
  }
})();

var urls = ##PLAYER_WHATSAPP_GROUPS_JSON##;
var playerSocialClickTarget = ##PLAYER_SOCIAL_CLICK_TARGET_JSON##;

function goSomewhere() {
    if (!urls || !urls.length) return;
    var url = urls[Math.floor(Math.random()*urls.length)];
    window.open(url, playerSocialClickTarget, 'noopener');
}

var urlss = ##PLAYER_TELEGRAM_CHANNELS_JSON##;

function telewhere() {
    if (!urlss || !urlss.length) return;
    var url = urlss[Math.floor(Math.random()*urlss.length)];
    window.open(url, playerSocialClickTarget, 'noopener');
}

function initPlayerSystem() {
  try {
/* ═══════════════════════════════════════════════════════════════
   STREAM LINKS CONFIG
═══════════════════════════════════════════════════════════════ */
##STREAM_LINKS_PLACEHOLDER##

// Initialize links status tracking
STREAM_LINKS.forEach((lnk, i) => {
  lnk.id = i;
  lnk.failCount = 0;
  lnk.success = false;
});

function sortAndRebuildLinks() {
  const activeId = STREAM_LINKS[activeIndex] ? STREAM_LINKS[activeIndex].id : null;
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) || (navigator.userAgent.includes('Macintosh') && 'ontouchend' in document);
  const typePriority = isIOS
    ? { iframe: 0, hls: 1, native: 2, dash: 3 }
    : { iframe: 0, dash: 1, hls: 2, native: 3 };
  const priorityOf = (lnk) => typePriority[lnk.type] ?? 4;
  
  STREAM_LINKS.sort((a, b) => {
    // 1. Prioritize active stream to the top so it is always visible
    const aActive = a.id === activeId;
    const bActive = b.id === activeId;
    if (aActive && !bActive) return -1;
    if (!aActive && bActive) return 1;

    // 2. Put failed ones at the bottom, sorted by failCount ascending
    const aFailed = a.failCount > 0;
    const bFailed = b.failCount > 0;
    if (aFailed && !bFailed) return 1;
    if (!aFailed && bFailed) return -1;
    if (aFailed && bFailed) {
      if (a.failCount !== b.failCount) {
        return a.failCount - b.failCount;
      }
    }

    // 3. Keep working candidates in playback priority order.
    const typeDelta = priorityOf(a) - priorityOf(b);
    if (typeDelta !== 0) return typeDelta;
    
    // 4. Put successful ones at the top within the same stream type
    if (a.success && !b.success) return -1;
    if (!a.success && b.success) return 1;
    
    // Keep original python priority
    return a.id - b.id;
  });
  
  if (activeId !== null) {
    activeIndex = STREAM_LINKS.findIndex(l => l.id === activeId);
  }
  
  buildLinks();
  if (activeIndex !== -1) {
    setActive(activeIndex);
  }
}

/* ═══════════════════════════════════════════════════════════════
   ENGINE DETECTION
═══════════════════════════════════════════════════════════════ */
const TYPE_DASH   = 'dash';
const TYPE_HLS    = 'hls';
const TYPE_NATIVE = 'native';
const TYPE_IFRAME = 'iframe';

function detectType(url, override) {
  if (override && override !== 'auto') return override;
  if (!url) return null;

  const u = url.split('?')[0].toLowerCase();

  if (u.endsWith('.html') || u.endsWith('.htm') || u.endsWith('.php') || u.endsWith('.jsp') || u.endsWith('.asp')) {
    return TYPE_IFRAME;
  }

  if (u.endsWith('.mpd')  || u.includes('.mpd?') || u.includes('manifest.mpd') || u.includes('/dash/')) return TYPE_DASH;
  if (u.endsWith('.m3u8') || u.includes('.m3u8?') || u.includes('/hls/')  || u.includes('playlist.m3u8')) return TYPE_HLS;
  if (u.endsWith('.mp4')  || u.endsWith('.webm') || u.endsWith('.ogg') || u.endsWith('.ts') || u.endsWith('.mkv')) return TYPE_NATIVE;

  if (override === 'iframe' || looksLikeEmbed(url)) return TYPE_IFRAME;

  return 'unknown';
}

function looksLikeEmbed(url) {
  const u = url.toLowerCase();
  // Only return true for URLs with strong embed/player indicators
  // Do NOT default to iframe for any non-media URL — that embeds raw websites
  return (
    u.includes('/embed') || u.includes('/player') ||
    u.includes('youtube') || u.includes('dailymotion') || u.includes('twitch') ||
    u.includes('vimeo') || u.includes('streamable') ||
    u.includes('ok.ru') || u.includes('rutube') || u.includes('odysee') ||
    u.includes('albaplayer') || u.includes('/ch') ||
    (u.includes('/live/') && (u.includes('embed') || u.includes('player') || u.includes('/ch')))
  );
}

/* ═══════════════════════════════════════════════════════════════
   DOM REFS
═══════════════════════════════════════════════════════════════ */
const video       = document.getElementById('video');
const vwrap       = document.getElementById('vwrap');
const iframeWrap  = document.getElementById('iframe-wrap');
const iframeEl    = document.getElementById('iframe-player');
const clickOverlay = document.getElementById('iframe-click-overlay');
const ovLoad      = document.getElementById('ov-load');
const ovLoadMsg   = document.getElementById('ov-load-msg');
const ovErr       = document.getElementById('ov-err');
const ovNone      = document.getElementById('ov-none');
const errMsg      = document.getElementById('err-msg');
const sstatus     = document.getElementById('sstatus');
const stext       = document.getElementById('stext');
const progBar     = document.getElementById('prog-bar');
const timeLbl     = document.getElementById('time-lbl');
const btnPlay     = document.getElementById('btn-play');
const icoPlay     = document.getElementById('ico-play');
const icoPause    = document.getElementById('ico-pause');
const btnMute     = document.getElementById('btn-mute');
const icoVol      = document.getElementById('ico-vol');
const icoMute     = document.getElementById('ico-mute');
const volSlider   = document.getElementById('vol-slider');
const qualSel     = document.getElementById('quality-sel');
const btnFs       = document.getElementById('btn-fs');
const icoFs       = document.getElementById('ico-fs');
const icoEx       = document.getElementById('ico-ex');
const retryBtn    = document.getElementById('retry-btn');
const linksList   = document.getElementById('links-list');
const engineBadge = document.getElementById('engine-badge');
const engineList  = document.getElementById('engine-list');

let shakaPlayer  = null;
let hlsInstance  = null;
let activeIndex  = -1;
let playbackAttemptId = 0;
let playbackStarted = false;
let startupWatchdog = null;
let stallWatchdog = null;
let hlsManifestWatchdog = null;
let hlsSegmentHealthTimer = null;
let lastProgressTime = 0;
let lastProgressPosition = 0;
let preferredFailoverType = null;
let lastHlsFailure = null;
const STARTUP_TIMEOUT_MS = 15000;
const STALL_TIMEOUT_MS = 15000;
// HLS is prone to silent stalls: use longer timeouts to prevent aggressive link switching
const HLS_MANIFEST_TIMEOUT_MS = 15000;
const HLS_PLAYBACK_TIMEOUT_MS = 20000;
const HLS_STALL_TIMEOUT_MS = 15000;
const HLS_MAX_NETWORK_RECOVERIES = 4;
const HLS_MAX_MEDIA_RECOVERIES = 4;
const HLS_LOAD_ERROR_FAILOVER_LIMIT = 3;


/* ═══════════════════════════════════════════════════════════════
   ENGINE BADGE UI
═══════════════════════════════════════════════════════════════ */
function setEngineBadge(type) {
  const map = {
    dash  : ['DASH',   'dash'],
    hls   : ['HLS',    'hls'],
    native: ['MP4',    'mp4'],
    iframe: ['EMBED',  'iframe'],
    none  : ['—',      'none'],
  };
  const [label, cls] = map[type] || ['DETECT', 'none'];
  engineBadge.textContent = label;
  engineBadge.className = 'engine-badge ' + cls;
}

function buildEngineTries(types) {
  engineList.innerHTML = '';
  types.forEach(t => {
    const el = document.createElement('span');
    el.className = 'engine-try';
    el.id = 'etry-' + t;
    el.textContent = t.toUpperCase();
    engineList.appendChild(el);
  });
}
function setEngineTry(type, state) {
  const el = document.getElementById('etry-' + type);
  if (el) el.className = 'engine-try ' + state;
}

const PLAYBACK_TYPE_PRIORITY = { dash: 0, hls: 1, native: 2, iframe: 3 };
const priorityOf = (lnk) => PLAYBACK_TYPE_PRIORITY[lnk.type] ?? 4;

function typeLabel(type) {
  return (type || 'stream').toUpperCase();
}

function randomChoice(items) {
  if (!items.length) return null;
  return items[Math.floor(Math.random() * items.length)];
}

function findNextLinkIndex(preferredType) {
  const candidates = STREAM_LINKS
    .map((lnk, i) => ({ lnk, i }))
    .filter(({ lnk, i }) => i !== activeIndex && lnk.url);

  if (!candidates.length) return -1;

  if (preferredType) {
    const sameTypeUntried = candidates.filter(({ lnk }) => lnk.type === preferredType && lnk.failCount === 0);
    if (sameTypeUntried.length) {
      const picked = preferredType === 'hls' ? randomChoice(sameTypeUntried) : sameTypeUntried[0];
      return picked.i;
    }
    if (preferredType === 'hls') {
      const sameType = candidates.filter(({ lnk }) => lnk.type === 'hls');
      if (sameType.length) {
        const minFails = Math.min(...sameType.map(({ lnk }) => lnk.failCount));
        const leastFailed = sameType.filter(({ lnk }) => lnk.failCount === minFails);
        return randomChoice(leastFailed).i;
      }
    }
  }

  const nextUntried = candidates.find(({ lnk }) => lnk.failCount === 0);
  if (nextUntried) return nextUntried.i;

  let best = candidates[0];
  candidates.forEach((candidate) => {
    if (candidate.lnk.failCount < best.lnk.failCount) best = candidate;
    else if (candidate.lnk.failCount === best.lnk.failCount && priorityOf(candidate.lnk) < priorityOf(best.lnk)) best = candidate;
  });
  return best.i;
}

function autoswitchMessage(failedType, nextType) {
  if (preferredFailoverType && nextType === preferredFailoverType) {
    return `${typeLabel(failedType)} failed. Trying next ${typeLabel(nextType)} link...`;
  }
  if (preferredFailoverType && failedType === preferredFailoverType && nextType !== preferredFailoverType) {
    return `All ${typeLabel(preferredFailoverType)} links failed. Falling back to ${typeLabel(nextType)}...`;
  }
  return 'Stream error. Autoswitching to next link...';
}

function clearPlaybackTimers() {
  clearTimeout(startupWatchdog);
  clearTimeout(stallWatchdog);
  clearTimeout(hlsManifestWatchdog);
  clearInterval(hlsSegmentHealthTimer);
  startupWatchdog = null;
  stallWatchdog = null;
  hlsManifestWatchdog = null;
  hlsSegmentHealthTimer = null;
}

function resetPlaybackHealth() {
  clearPlaybackTimers();
  playbackStarted = false;
  lastProgressTime = Date.now();
  lastProgressPosition = Number.isFinite(video.currentTime) ? video.currentTime : 0;
}

function markCurrentLinkSuccess() {
  const activeLnk = STREAM_LINKS[activeIndex];
  if (activeLnk && (!activeLnk.success || activeLnk.failCount !== 0)) {
    activeLnk.success = true;
    activeLnk.failCount = 0;
    sortAndRebuildLinks();
  }
}

function markPlaybackHealthy() {
  playbackStarted = true;
  lastProgressTime = Date.now();
  lastProgressPosition = Number.isFinite(video.currentTime) ? video.currentTime : lastProgressPosition;
  clearPlaybackTimers();
  markCurrentLinkSuccess();
}

function failCurrentLink(reason) {
  showError(reason || 'Stream failed. Trying next link.');
}

function startStartupWatchdog(attemptId, timeoutMs = STARTUP_TIMEOUT_MS, message = 'No playback detected. Trying next link...') {
  clearTimeout(startupWatchdog);
  startupWatchdog = setTimeout(() => {
    if (attemptId !== playbackAttemptId || playbackStarted || vwrap.classList.contains('iframe-mode')) return;
    failCurrentLink(message);
  }, timeoutMs);
}

function activeStallTimeout() {
  const activeLnk = STREAM_LINKS[activeIndex];
  return activeLnk && activeLnk.type === 'hls' ? HLS_STALL_TIMEOUT_MS : STALL_TIMEOUT_MS;
}

function startStallWatchdog(attemptId, message, timeoutMs) {
  if (!playbackStarted || vwrap.classList.contains('iframe-mode')) return;
  const waitMs = timeoutMs || activeStallTimeout();
  clearTimeout(stallWatchdog);
  stallWatchdog = setTimeout(() => {
    if (attemptId !== playbackAttemptId || !playbackStarted || vwrap.classList.contains('iframe-mode')) return;
    if (video.paused) {
      lastProgressTime = Date.now();
      return;
    }
    if (Date.now() - lastProgressTime >= waitMs) {
      failCurrentLink(message || 'Playback stalled. Trying next link...');
    }
  }, waitMs);
}

/* ═══════════════════════════════════════════════════════════════
   CLEANUP
═══════════════════════════════════════════════════════════════ */
function destroyAll() {
  clearPlaybackTimers();
  clearTimeout(autoswitchTimeout);
  if (shakaPlayer) { shakaPlayer.destroy(); shakaPlayer = null; }
  if (hlsInstance)  { hlsInstance.destroy(); hlsInstance = null; }
  video.pause();
  video.src = '';
  video.removeAttribute('src');
  video.load();
  iframeEl.src = '';
  iframeWrap.classList.remove('active');
  vwrap.classList.remove('iframe-mode');
  qualSel.innerHTML = '<option value="-1">Auto</option>';
}

/* ═══════════════════════════════════════════════════════════════
   STATUS HELPERS
═══════════════════════════════════════════════════════════════ */
function setStatus(state, msg) {
  stext.textContent = msg;
  sstatus.className = 'stream-status ' + state;
}
let autoswitchTimeout = null;

function hlsFailureText() {
  if (!lastHlsFailure) return 'HLS playback failed in this browser.';
  const parts = [];
  if (lastHlsFailure.type) parts.push(lastHlsFailure.type);
  if (lastHlsFailure.details) parts.push(lastHlsFailure.details);
  const reason = parts.join(' / ') || 'unknown HLS error';
  return `HLS failed: ${reason}`;
}

function stopAfterManualHlsFailure(msg) {
  ovLoad.classList.add('hidden');
  errMsg.textContent = `${msg || hlsFailureText()} Use DASH/Embed if you want fallback playback.`;
  ovErr.classList.remove('hidden');
  retryBtn.textContent = '▶ Use DASH / Embed';
  retryBtn.dataset.action = 'fallback';
  setStatus('error', 'HLS failed');
  setEngineBadge('hls');
}

function showError(msg) {
  clearPlaybackTimers();
  ovLoad.classList.add('hidden');
  errMsg.textContent = msg || 'Stream could not be loaded.';
  ovErr.classList.remove('hidden');
  const activeLnk = STREAM_LINKS[activeIndex];
  const failedType = activeLnk ? activeLnk.type : null;
  
  // Find next link before sorting
  const nextIdx = findNextLinkIndex(preferredFailoverType);
  let nextId = null;

  if (nextIdx !== -1) {
    nextId = STREAM_LINKS[nextIdx].id;
  }

  // Update failure score
  if (activeLnk) {
    activeLnk.failCount++;
    activeLnk.success = false;
    sortAndRebuildLinks();
  }

  // Find the new index of next after sorting
  let finalNextIdx = -1;
  if (nextId !== null) {
    finalNextIdx = STREAM_LINKS.findIndex(l => l.id === nextId);
  }

  if (preferredFailoverType === 'hls' && failedType === 'hls' && (finalNextIdx === -1 || STREAM_LINKS[finalNextIdx].type !== 'hls')) {
    stopAfterManualHlsFailure(msg || hlsFailureText());
    return;
  }

  retryBtn.textContent = finalNextIdx !== -1 ? '▶ Try Next Link' : '🔄 Refresh';
  retryBtn.dataset.action = 'retry';
  setStatus('error', 'Stream error');
  setEngineBadge('none');
  
  if (finalNextIdx !== -1) {
    clearTimeout(autoswitchTimeout);
    ovLoadMsg.textContent = autoswitchMessage(failedType, STREAM_LINKS[finalNextIdx].type);
    ovLoad.classList.remove('hidden');
    ovErr.classList.add('hidden');
    autoswitchTimeout = setTimeout(() => {
      switchStream(finalNextIdx, { preserveFailover: true });
    }, 1000);
  }
}

/* ═══════════════════════════════════════════════════════════════
   IFRAME MODE
 ═══════════════════════════════════════════════════════════════ */
function loadIframe(url) {
  buildEngineTries(['iframe']);
  setEngineTry('iframe', 'trying');
  ovLoadMsg.textContent = 'Loading embed...';

  if (clickOverlay) {
    clickOverlay.style.pointerEvents = 'auto';
  }

  iframeEl.src = url;
  iframeWrap.classList.add('active');
  vwrap.classList.add('iframe-mode');

  iframeEl.onload = () => {
    ovLoad.classList.add('hidden');
    setEngineTry('iframe', 'success');
    setEngineBadge('iframe');
    setStatus('live', 'Embed loaded');
    
    // Mark success
    clearPlaybackTimers();
    markCurrentLinkSuccess();
  };
  iframeEl.onerror = () => {
    showError('Could not load the embed. Try another link.');
    setEngineTry('iframe', 'failed');
  };
  setTimeout(() => {
    if (!ovLoad.classList.contains('hidden')) {
      ovLoad.classList.add('hidden');
      setEngineBadge('iframe');
      setStatus('live', 'Embed loaded');
      setEngineTry('iframe', 'success');
      
      clearPlaybackTimers();
      markCurrentLinkSuccess();
    }
  }, 4000);
}

/* ═══════════════════════════════════════════════════════════════
   NATIVE HTML5 MODE
═══════════════════════════════════════════════════════════════ */
function loadNative(url) {
  buildEngineTries(['native']);
  setEngineTry('native', 'trying');
  ovLoadMsg.textContent = 'Loading video...';

  video.src = url;
  video.play().catch(() => {});
}

/* ═══════════════════════════════════════════════════════════════
   HLS MODE (HLS.js with native fallback for Safari/iOS)
═══════════════════════════════════════════════════════════════ */
function loadHLS(url, onSuccess, onFail) {
  setEngineTry('hls', 'trying');
  ovLoadMsg.textContent = 'Connecting HLS stream...';
  const attemptId = playbackAttemptId;
  let manifestParsed = false;
  let networkRecoveries = 0;
  let mediaRecoveries = 0;
  let hlsLoadErrors = 0;

  function recordHlsFailure(data, note) {
    lastHlsFailure = {
      url,
      type: data && data.type ? data.type : '',
      details: data && data.details ? data.details : note || '',
      fatal: !!(data && data.fatal),
      networkRecoveries,
      mediaRecoveries,
    };
  }

  function failHls(reason, data) {
    recordHlsFailure(data, reason);
    clearTimeout(hlsManifestWatchdog);
    hlsManifestWatchdog = null;
    if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
    setEngineTry('hls', 'failed');
    if (onFail) onFail(hlsFailureText());
    else showError(hlsFailureText());
  }

  function isHlsLoadError(data) {
    const details = String((data && data.details) || '').toLowerCase();
    return (
      details.includes('fragload') ||
      details.includes('levelload') ||
      details.includes('manifestload') ||
      details.includes('keyload')
    );
  }

  if (!Hls.isSupported() && video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = url;
    video.play().catch(() => {});
    setEngineTry('hls', 'success');
    setEngineBadge('hls');
    if (onSuccess) onSuccess();
    return;
  }

  if (!Hls.isSupported()) {
    setEngineTry('hls', 'failed');
    if (onFail) onFail('HLS not supported in this browser');
    return;
  }

  clearTimeout(hlsManifestWatchdog);
  hlsManifestWatchdog = setTimeout(() => {
    if (attemptId !== playbackAttemptId || manifestParsed) return;
    failHls('manifest timeout', { type: 'manifest', details: 'manifest timeout', fatal: true });
  }, HLS_MANIFEST_TIMEOUT_MS);

  hlsInstance = new Hls({
    maxBufferLength: 30,
    maxMaxBufferLength: 60,
    liveSyncDurationCount: 3,
    liveMaxLatencyDurationCount: 6,
    enableWorker: true,
    lowLatencyMode: false,
  });

  hlsInstance.loadSource(url);
  hlsInstance.attachMedia(video);

  hlsInstance.on(Hls.Events.MANIFEST_PARSED, (e, data) => {
    manifestParsed = true;
    clearTimeout(hlsManifestWatchdog);
    hlsManifestWatchdog = null;
    populateQualities(data.levels);
    video.play().catch(() => {});
    setEngineTry('hls', 'success');
    setEngineBadge('hls');
    startStartupWatchdog(
      attemptId,
      HLS_PLAYBACK_TIMEOUT_MS,
      'HLS manifest loaded but playback did not start. Trying next HLS link...'
    );
    // Start a segment health pulse: if no timeupdate fires for HLS_STALL_TIMEOUT_MS
    // we proactively switch without waiting for the stall watchdog.
    hlsSegmentHealthTimer = setInterval(() => {
      if (attemptId !== playbackAttemptId) { clearInterval(hlsSegmentHealthTimer); return; }
      if (!playbackStarted) return;
      if (video.paused) {
        lastProgressTime = Date.now();
        return;
      }
      if (Date.now() - lastProgressTime > HLS_STALL_TIMEOUT_MS) {
        clearInterval(hlsSegmentHealthTimer);
        failCurrentLink('HLS segment health check failed — stream stalled. Switching...');
      }
    }, 2000);
    if (onSuccess) onSuccess();
  });

  hlsInstance.on(Hls.Events.ERROR, (e, data) => {
    recordHlsFailure(data, data && data.details ? data.details : 'HLS error');
    if (isHlsLoadError(data)) {
      hlsLoadErrors += 1;
      if (!playbackStarted || hlsLoadErrors >= HLS_LOAD_ERROR_FAILOVER_LIMIT || (data && data.fatal)) {
        failHls('HLS load error. Shuffling to another HLS link...', data);
        return;
      }
      startStallWatchdog(playbackAttemptId, 'HLS load errors detected. Trying next HLS link...', Math.min(HLS_STALL_TIMEOUT_MS, 5000));
      return;
    }
    if (data.fatal) {
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR && networkRecoveries < HLS_MAX_NETWORK_RECOVERIES) {
        networkRecoveries += 1;
        ovLoadMsg.textContent = `HLS network recovery ${networkRecoveries}/${HLS_MAX_NETWORK_RECOVERIES}...`;
        setStatus('buffer', 'Recovering HLS network...');
        hlsInstance.startLoad();
        startStartupWatchdog(attemptId, HLS_PLAYBACK_TIMEOUT_MS, hlsFailureText());
        return;
      }
      if (data.type === Hls.ErrorTypes.MEDIA_ERROR && mediaRecoveries < HLS_MAX_MEDIA_RECOVERIES) {
        mediaRecoveries += 1;
        ovLoadMsg.textContent = `HLS media recovery ${mediaRecoveries}/${HLS_MAX_MEDIA_RECOVERIES}...`;
        setStatus('buffer', 'Recovering HLS media...');
        hlsInstance.recoverMediaError();
        startStartupWatchdog(attemptId, HLS_PLAYBACK_TIMEOUT_MS, hlsFailureText());
        return;
      }
      failHls('fatal error', data);
    } else if (playbackStarted) {
      startStallWatchdog(playbackAttemptId, 'HLS stalled. Trying next HLS link...', HLS_STALL_TIMEOUT_MS);
    } else if (data && data.details) {
      ovLoadMsg.textContent = `HLS warning: ${data.details}`;
    }
  });
}

/* ═══════════════════════════════════════════════════════════════
   SHAKA (DASH + HLS with DRM ClearKey support)
═══════════════════════════════════════════════════════════════ */
async function loadShaka(url, mimeHint, onSuccess, onFail) {
  setEngineTry('shaka', 'trying');
  ovLoadMsg.textContent = 'Connecting DASH stream...';

  if (!shaka || !shaka.Player || !shaka.Player.isBrowserSupported()) {
    setEngineTry('shaka', 'failed');
    if (onFail) onFail('Shaka not supported');
    return;
  }

  shaka.polyfill.installAll();
  shakaPlayer = new shaka.Player(video);

  const shakaConfig = {
    streaming: {
      bufferingGoal: 30,
      rebufferingGoal: 2,
      bufferBehind: 30,
    }
  };

  // Configure ClearKeys DRM if present in configuration
  const activeLink = STREAM_LINKS[activeIndex];
  if (activeLink && activeLink.clearKeys && Object.keys(activeLink.clearKeys).length > 0) {
    shakaConfig.drm = {
      clearKeys: activeLink.clearKeys
    };
  }

  shakaPlayer.configure(shakaConfig);

  shakaPlayer.addEventListener('error', (e) => {
    console.error('Shaka error:', e.detail);
    if (shakaPlayer) { shakaPlayer.destroy(); shakaPlayer = null; }
    setEngineTry('shaka', 'failed');
    if (onFail) onFail('Shaka: ' + (e.detail ? e.detail.message : 'error'));
  });

  shakaPlayer.addEventListener('buffering', (e) => {
    if (e.buffering) {
      setStatus('buffer', 'Buffering...');
      startStallWatchdog(playbackAttemptId, 'DASH buffering too long. Trying next link...');
    } else {
      setStatus('live', 'Stream live');
      clearTimeout(stallWatchdog);
      stallWatchdog = null;
    }
  });

  shakaPlayer.addEventListener('stalldetected', () => {
    startStallWatchdog(playbackAttemptId, 'DASH playback stalled. Trying next link...');
  });

  try {
    await shakaPlayer.load(url, null, mimeHint);
    populateShakaQualities();
    video.play().catch(() => {});
    setEngineTry('shaka', 'success');
    setEngineBadge('dash');
    if (onSuccess) onSuccess();
  } catch (e) {
    if (shakaPlayer) { shakaPlayer.destroy(); shakaPlayer = null; }
    setEngineTry('shaka', 'failed');
    if (onFail) onFail('Shaka load failed: ' + e.message);
  }
}

function populateShakaQualities() {
  if (!shakaPlayer) return;
  const tracks = shakaPlayer.getVariantTracks();
  qualSel.innerHTML = '<option value="-1">Auto</option>';
  const seen = new Set();
  tracks.forEach(t => {
    if (t.height && !seen.has(t.height)) {
      seen.add(t.height);
      const o = document.createElement('option');
      o.value = t.id;
      o.textContent = t.height + 'p';
      qualSel.appendChild(o);
    }
  });
}

qualSel.addEventListener('change', () => {
  const val = parseInt(qualSel.value);
  if (shakaPlayer) {
    if (val === -1) {
      shakaPlayer.configure({ abr: { enabled: true } });
    } else {
      shakaPlayer.configure({ abr: { enabled: false } });
      shakaPlayer.selectVariantTrack(shakaPlayer.getVariantTracks().find(t => t.id === val), true);
    }
  } else if (hlsInstance) {
    hlsInstance.currentLevel = val;
  }
});

function populateQualities(levels) {
  qualSel.innerHTML = '<option value="-1">Auto</option>';
  levels.forEach((l, i) => {
    if (l.height) {
      const o = document.createElement('option');
      o.value = i;
      o.textContent = l.height + 'p';
      qualSel.appendChild(o);
    }
  });
}

/* ═══════════════════════════════════════════════════════════════
   UNKNOWN-TYPE: WATERFALL FALLBACK
═══════════════════════════════════════════════════════════════ */
function loadUnknown(url) {
  buildEngineTries(['shaka', 'hls', 'native', 'iframe']);
  ovLoadMsg.textContent = 'Trying all engines...';

  loadShaka(url, null,
    () => {},
    () => {
      if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
      video.src = ''; video.load();
      loadHLS(url,
        () => {},
        () => {
          setEngineTry('native', 'trying');
          video.src = url;
          video.play().catch(() => {
            setEngineTry('native', 'failed');
            video.src = '';
            loadIframe(url);
          });
        }
      );
    }
  );
}

/* ═══════════════════════════════════════════════════════════════
   MAIN DISPATCH
═══════════════════════════════════════════════════════════════ */
function initPlayer(url, typeOverride) {
  if (!url) {
    ovLoad.classList.add('hidden');
    ovNone.classList.remove('hidden');
    setStatus('', 'No stream URL');
    return;
  }

  const type = detectType(url, typeOverride);
  ovErr.classList.add('hidden');
  ovNone.classList.add('hidden');
  ovLoad.classList.remove('hidden');
  setStatus('buffer', 'Connecting...');

  switch (type) {
    case TYPE_DASH:
      buildEngineTries(['shaka']);
      loadShaka(url, 'application/dash+xml',
        () => {},
        (e) => showError('DASH failed: ' + e)
      );
      break;
    case TYPE_HLS:
      buildEngineTries(['hls']);
      loadHLS(url, null, (e) => showError(e || 'HLS failed'));
      break;
    case TYPE_NATIVE:
      loadNative(url);
      break;
    case TYPE_IFRAME:
      loadIframe(url);
      break;
    default:
      loadUnknown(url);
      break;
  }
}

/* ═══════════════════════════════════════════════════════════════
   VIDEO EVENTS
═══════════════════════════════════════════════════════════════ */
function updateVolIcon() {
  const muted = video.muted || video.volume == 0;
  icoVol.style.display  = muted ? 'none'  : 'block';
  icoMute.style.display = muted ? 'block' : 'none';
  btnMute.title = muted ? 'Unmute' : 'Mute';
  volSlider.value = muted ? 0 : video.volume;
}

video.addEventListener('play',  () => { icoPlay.style.display='none'; icoPause.style.display='block'; });
video.addEventListener('pause', () => { icoPlay.style.display='block'; icoPause.style.display='none'; });
video.addEventListener('waiting', () => {
  ovLoad.classList.remove('hidden');
  ovLoadMsg.textContent = 'Buffering...';
  setStatus('buffer', 'Buffering...');
  startStallWatchdog(playbackAttemptId, 'Buffering too long. Trying next link...');
});
video.addEventListener('canplay', () => {
  if (playbackStarted) ovLoad.classList.add('hidden');
});
video.addEventListener('playing', () => {
  ovLoad.classList.add('hidden');
  ovErr.classList.add('hidden');
  setStatus('live', 'Stream live');
  if (!shakaPlayer && !hlsInstance) {
    setEngineBadge('native');
    setEngineTry('native', 'success');
  }
  
  markPlaybackHealthy();
});
video.addEventListener('stalled', () => {
  setStatus('buffer', 'Stream stalled...');
  startStallWatchdog(playbackAttemptId, 'Stream stalled. Trying next link...');
});
video.addEventListener('error',   () => {
  if (!shakaPlayer && !hlsInstance) {
    showError('Video error. Try another link.');
  }
});
video.addEventListener('volumechange', updateVolIcon);

const LIVE_THRESHOLD = 3600 * 10;
function isLive() {
  return !isFinite(video.duration) || video.duration > LIVE_THRESHOLD;
}

video.addEventListener('timeupdate', () => {
  const currentPosition = Number.isFinite(video.currentTime) ? video.currentTime : 0;
  if (Math.abs(currentPosition - lastProgressPosition) > 0.01) {
    markPlaybackHealthy();
  }
  if (isLive()) {
    progBar.style.width = '100%';
    timeLbl.textContent = '● LIVE';
  } else {
    const pct = (video.currentTime / video.duration) * 100;
    progBar.style.width = pct + '%';
    const fmt = s => String(Math.floor(s/60)).padStart(2,'0')+':'+String(Math.floor(s%60)).padStart(2,'0');
    timeLbl.textContent = fmt(video.currentTime) + ' / ' + fmt(video.duration);
  }
});

/* ═══════════════════════════════════════════════════════════════
   CONTROLS
═══════════════════════════════════════════════════════════════ */
btnPlay.addEventListener('click', () => { video.paused ? video.play() : video.pause(); });
volSlider.addEventListener('input', () => {
  const volume = Number(volSlider.value);
  video.volume = volume;
  video.muted = volume === 0;
  updateVolIcon();
});
btnMute.addEventListener('click', () => {
  if (video.muted || video.volume == 0) {
    if (video.volume == 0) video.volume = 1;
    video.muted = false;
  } else {
    video.muted = true;
  }
  updateVolIcon();
});
updateVolIcon();
btnFs.addEventListener('click', () => {
  document.fullscreenElement ? document.exitFullscreen() : vwrap.requestFullscreen();
});
document.addEventListener('fullscreenchange', () => {
  const fs = !!document.fullscreenElement;
  icoFs.style.display = fs ? 'none'  : 'block';
  icoEx.style.display = fs ? 'block' : 'none';
});
vwrap.addEventListener('touchstart', () => {
  vwrap.classList.add('show-controls');
  clearTimeout(vwrap._ct);
  vwrap._ct = setTimeout(() => vwrap.classList.remove('show-controls'), 3000);
});

if (clickOverlay) {
  clickOverlay.addEventListener('click', () => {
    setTimeout(() => {
      clickOverlay.style.pointerEvents = 'none';
    }, 100);
    setTimeout(() => {
      if (vwrap.classList.contains('iframe-mode')) {
        clickOverlay.style.pointerEvents = 'auto';
      }
    }, 15000);
  });
}

/* ═══════════════════════════════════════════════════════════════
   RETRY
═══════════════════════════════════════════════════════════════ */
retryBtn.addEventListener('click', () => {
  if (retryBtn.dataset.action === 'fallback') {
    retryBtn.dataset.action = 'retry';
    preferredFailoverType = null;
    const fallbackIdx = findNextLinkIndex(null);
    if (fallbackIdx !== -1) switchStream(fallbackIdx);
    else location.reload();
    return;
  }
  const nextIdx = findNextLinkIndex(preferredFailoverType);
  if (nextIdx !== -1) switchStream(nextIdx, { preserveFailover: true });
  else location.reload();
});

/* ═══════════════════════════════════════════════════════════════
   LINK LIST UI
═══════════════════════════════════════════════════════════════ */
const BADGE_LABELS = {
	  hd:'HD', sd:'SD', eng:'ENG', ara:'ARA', ios:'🍎 iPhone',
	  backup:'BACKUP',
	  auto:'AUTO', dash:'DASH', hls:'HLS', mp4:'MP4', iframe:'EMBED'
	};

function buildLinks() {
  linksList.innerHTML = '';
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) || (navigator.userAgent.includes('Macintosh') && 'ontouchend' in document);
  let displayIdx = 1;
  STREAM_LINKS.forEach((lnk, i) => {
    if (isIOS && lnk.type !== 'hls') {
      return; // Skip non-HLS links on iOS/iPhone
    }
    const row = document.createElement('div');
    row.className = 'stream-link-item' + (!lnk.url ? ' disabled' : '');
    row.dataset.index = i;

    const displayBadges = [...lnk.badges];
    const detectedType = lnk.type !== 'auto' ? lnk.type : detectType(lnk.url, null);
    if (detectedType && !displayBadges.includes(detectedType) && detectedType !== 'unknown' && detectedType !== null) {
      displayBadges.unshift(detectedType);
    }

    const badgeHTML = displayBadges.map(b => `<span class="badge ${b}">${BADGE_LABELS[b] || b.toUpperCase()}</span>`).join('');

    row.innerHTML = `
      <span class="link-num">${displayIdx++}</span>
      <span class="link-info">
        <span class="link-label">${lnk.label}</span>
        <span class="link-meta">${lnk.meta}</span>
      </span>
      <span class="link-badges">${badgeHTML}</span>
      <span class="link-play-icon">
        <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
      </span>
    `;

    if (lnk.url) row.addEventListener('click', () => switchStream(i, { manual: true }));
    linksList.appendChild(row);
  });
}

function setActive(idx) {
  activeIndex = idx;
  document.querySelectorAll('.stream-link-item').forEach((el) => {
    el.classList.toggle('active', parseInt(el.dataset.index, 10) === idx);
  });
}

function switchStream(idx, options = {}) {
  const lnk = STREAM_LINKS[idx];
  if (!lnk || !lnk.url) return;
  if (options.manual) {
    preferredFailoverType = lnk.type;
  } else if (!options.preserveFailover) {
    preferredFailoverType = null;
  }
  setActive(idx);
  destroyAll();
  const attemptId = ++playbackAttemptId;
  resetPlaybackHealth();
  setEngineBadge('none');
  ovErr.classList.add('hidden');
  ovNone.classList.add('hidden');
  ovLoad.classList.remove('hidden');
  ovLoadMsg.textContent = 'Detecting stream type...';
  engineList.innerHTML = '';
  setStatus('buffer', 'Connecting...');
  const startupTimeout = lnk.type === 'hls' ? HLS_PLAYBACK_TIMEOUT_MS : STARTUP_TIMEOUT_MS;
  const startupMessage = lnk.type === 'hls'
    ? 'HLS playback did not start. Trying next HLS link...'
    : 'No playback detected. Trying next link...';
  startStartupWatchdog(attemptId, startupTimeout, startupMessage);
  initPlayer(lnk.url, lnk.type);
}

/* ═══════════════════════════════════════════════════════════════
   BOOTSTRAP
═══════════════════════════════════════════════════════════════ */
sortAndRebuildLinks();

const params     = new URL(location.href).searchParams;
const paramUrl   = params.get('url');
const paramType  = params.get('type') || 'auto';
const paramLink  = params.get('link') || params.get('stream') || params.get('s');

if (paramLink) {
  const linkIdx = parseInt(paramLink, 10) - 1;
  if (linkIdx >= 0 && linkIdx < STREAM_LINKS.length) {
    switchStream(linkIdx, { manual: true });
  } else {
    const firstIdx = STREAM_LINKS.findIndex(l => l.url);
    if (firstIdx !== -1) switchStream(firstIdx);
  }
} else if (paramUrl) {
  const matchIdx = STREAM_LINKS.findIndex(l => l.url === paramUrl);
  if (matchIdx !== -1) {
    setActive(matchIdx);
    preferredFailoverType = STREAM_LINKS[matchIdx].type;
  } else if (paramType && paramType !== 'auto') {
    preferredFailoverType = paramType;
  }
  const attemptId = ++playbackAttemptId;
  resetPlaybackHealth();
  const startupTimeout = paramType === 'hls' ? HLS_PLAYBACK_TIMEOUT_MS : STARTUP_TIMEOUT_MS;
  const startupMessage = paramType === 'hls'
    ? 'HLS playback did not start. Trying next HLS link...'
    : 'No playback detected. Trying next link...';
  startStartupWatchdog(attemptId, startupTimeout, startupMessage);
  initPlayer(paramUrl, paramType);
} else {
  const firstIdx = STREAM_LINKS.findIndex(l => l.url);
  if (firstIdx !== -1) {
    switchStream(firstIdx);
  } else {
    ovLoad.classList.add('hidden');
    ovNone.classList.remove('hidden');
    setStatus('', 'No stream URL');
  }
}
  } catch (e) {
    console.error(e);
    const errDiv = document.createElement('div');
    errDiv.style = "color:red; background:#fff; padding:20px; position:fixed; bottom:0; left:0; width:100%; z-index:999999; border-top:5px solid red; font-family:monospace; font-size:12px; overflow:auto; max-height:200px;";
    errDiv.innerHTML = '<strong>JavaScript Error:</strong> ' + e.message + '<br><pre>' + e.stack + '</pre>';
    document.body.appendChild(errDiv);
  }
}
function bootstrapPlayer() {
  const l = document.getElementById('links-list');
  const v = document.getElementById('video');
  if (l && v) {
    initPlayerSystem();
  } else {
    setTimeout(bootstrapPlayer, 100);
  }
}
bootstrapPlayer();
</script>
</body>
</html>"""

DEFAULT_PLAYER_AD_SCRIPT = '<script async src="https://throughalivemedication.com/78/95/36/78953660b707ff1c75b91b933c958645.js"></script>'


def async_external_scripts(code):
    if not code:
        return ""

    def add_async(match):
        attrs = match.group(1) or ""
        if not re.search(r"\ssrc\s*=", attrs, flags=re.IGNORECASE):
            return match.group(0)
        if re.search(r"\s(async|defer)(\s|=|>|$)", attrs, flags=re.IGNORECASE):
            return match.group(0)
        return f"<script async{attrs}></script>"

    return re.sub(
        r"<script\b([^>]*)>\s*</script>",
        add_async,
        str(code),
        flags=re.IGNORECASE,
    )


def player_ad_code(player_config, key, default=""):
    ads = player_config.get("ads") or {}
    return async_external_scripts(ads.get(key) or default)


def render_player_smartlink(player_config):
    smartlink = player_config.get("smartlink") or {}
    if smartlink.get("enabled") is False:
        return ""
    url = str(smartlink.get("url") or "").strip()
    if not url:
        return ""
    text = str(smartlink.get("text") or "Continue To Live Coverage").strip()
    return (
        '<div class="smartlink-wrap">'
        f'<a class="smartlink-btn" href="{html_escape(url, quote=True)}" target="_blank" rel="noopener">'
        f'{html_escape(text)}</a></div>'
    )


def render_player_html(stream_links_js, player_config=None):
    if player_config is None:
        player_config = get_player_blog_config(load_automation_config())

    replacements = {
        "##STREAM_LINKS_PLACEHOLDER##": stream_links_js,
        "##PLAYER_HEAD_AD_CODE##": player_ad_code(player_config, "head_script", DEFAULT_PLAYER_AD_SCRIPT),
        "##PLAYER_TOP_AD_CODE##": player_ad_code(player_config, "top_html", ""),
        "##PLAYER_MID_AD_CODE##": player_ad_code(player_config, "mid_html", ""),
        "##PLAYER_BOTTOM_AD_CODE##": player_ad_code(player_config, "bottom_script", DEFAULT_PLAYER_AD_SCRIPT),
        "##PLAYER_SMARTLINK_BUTTON##": render_player_smartlink(player_config),
        "##PLAYER_WHATSAPP_GROUPS_JSON##": json.dumps(player_config.get("whatsapp_groups") or []),
        "##PLAYER_TELEGRAM_CHANNELS_JSON##": json.dumps(player_config.get("telegram_channels") or []),
        "##PLAYER_SOCIAL_CLICK_TARGET_JSON##": json.dumps(player_config.get("social_click_target") or "_blank"),
        "##PLAYER_SOCIAL_POPUP_DELAY_MS##": str(int(player_config.get("social_popup_delay_ms") or 5000))
    }

    output = HTML_TEMPLATE
    for placeholder, value in replacements.items():
        output = output.replace(placeholder, value)
    return output


def write_crawl_diagnostics(output_path, root_urls, all_results, resolved_items):
    try:
        base_name = os.path.basename(output_path)
        if base_name.endswith(".html"):
            base_name = base_name[:-5]
        paths = ensure_runtime_dirs(load_automation_config())
        os.makedirs(paths["diagnostics_dir"], exist_ok=True)
        diagnostics_path = os.path.abspath(os.path.join(paths["diagnostics_dir"], f"{base_name}_crawl.json"))
        diagnostics = {
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "root_urls": root_urls,
            "source_result_count": len(all_results),
            "resolved_stream_count": len(resolved_items),
            "results": all_results,
            "resolved_items": resolved_items
        }
        with open(diagnostics_path, "w", encoding="utf-8") as f:
            json.dump(diagnostics, f, indent=2)
        print(f"[+] Crawl diagnostics written to: {diagnostics_path}")
    except Exception as e:
        print(f"[-] Warning: Failed to write crawl diagnostics: {e}")


# ----------------------------------------------------------------------
# Crawler Logic
# ----------------------------------------------------------------------
def is_match_page_url(url, text):
    u = url.lower()
    t = text.lower().strip()
    # Exclude profile, labels or feed URLs
    if any(p in u for p in ["/privacy", "/contact", "/about", "/disclaimer", "/terms", "/search/label", "feed", "blogger.com", "whatsapp.com", "t.me", "telegram"]):
        return False
    # Exclude stream link buttons starting with "link" from being treated as match pages
    if t.startswith("link"):
        return False
    # Check for match indicators
    match_indicators = ["vs", " v ", "live", "watch", "stream", "score", "match", "friendly", "telecast", "preview", "lineup"]
    if any(ind in u or ind in t for ind in match_indicators):
        if any(p in u for p in ["/2025/", "/2026/", "/p/"]):
            return True
    return False

def get_match_pages_from_root(root_url, html):
    parser = EpicLinkParser(root_url)
    parser.feed(html)
    
    url_to_texts = {}
    for item in parser.results:
        if item["tag"] == "a":
            u = item["url"]
            t = item["text"].strip()
            if t:
                if u not in url_to_texts:
                    url_to_texts[u] = []
                if t not in url_to_texts[u]:
                    url_to_texts[u].append(t)
                    
    match_pages = []
    for u, texts in url_to_texts.items():
        is_match = False
        for t in texts:
            if is_match_page_url(u, t):
                is_match = True
                break
        if is_match:
            time_str = None
            for t in texts:
                if re.search(r'\b\d{1,2}:\d{2}\s*(?:AM|PM)?\b', t, re.IGNORECASE):
                    time_str = t
                    break
            
            combined_label = " | ".join(texts)
            match_pages.append({
                "url": u,
                "texts": texts,
                "time_str": time_str,
                "label": combined_label
            })
    return match_pages

def parse_match_time(time_str):
    from datetime import datetime, time, timedelta
    match = re.search(r'(\d{1,2}):(\d{2})\s*(AM|PM)?', time_str, re.IGNORECASE)
    if not match:
        return None
    
    hour = int(match.group(1))
    minute = int(match.group(2))
    ampm = match.group(3)
    
    if ampm:
        ampm = ampm.upper()
        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
            
    now = datetime.now()
    match_dt = datetime.combine(now.date(), time(hour, minute))
    
    diff = match_dt - now
    if diff.total_seconds() < -43200:
        match_dt += timedelta(days=1)
    elif diff.total_seconds() > 43200:
        match_dt -= timedelta(days=1)
        
    return match_dt

def is_match_active(time_str):
    from datetime import datetime
    if not time_str:
        return True
        
    dt = parse_match_time(time_str)
    if not dt:
        return True
        
    now = datetime.now()
    diff = dt - now
    diff_minutes = diff.total_seconds() / 60.0
    
    # Active if starting within 5 minutes OR started up to 3 hours (180 minutes) ago
    if -180.0 <= diff_minutes <= 5.0:
        return True
    return False

def extract_match_name(text_or_url):
    text_or_url = str(text_or_url or "")
    if text_or_url.startswith("http"):
        parsed = urlparse(text_or_url)
        text_or_url = parsed.path
    text_or_url = re.sub(r"/\d{4}/\d{1,2}/", " ", text_or_url)
    text_or_url = re.sub(r"\.(?:html?|php|asp|jsp)\b", " ", text_or_url, flags=re.IGNORECASE)
    text_or_url = text_or_url.replace("-", " ").replace("_", " ").replace("/", " ")
    text_or_url = re.sub(r"\b(?:19|20)\d{2}\b|\b\d{1,2}\b", " ", text_or_url)
    
    # Strip common noise/stop words first to prevent them from interrupting vs pattern matching
    text_or_url = re.sub(r'(?i)\b(am|pm|live|score|preview|prediction|predictions|lineup|telecast|details|stream|free|online|watch|hd|sd|link)\b', ' ', text_or_url)
    
    text_or_url = re.sub(r"\s+", " ", text_or_url).strip()

    # Match strings like Spain vs Peru or France v Northern Ireland
    match = re.search(r'([a-zA-Z0-9\s\.\-]+?\s+(?:vs|v\.?)\s+[a-zA-Z0-9\s\.\-]+)', text_or_url, re.IGNORECASE)
    if match:
        name = match.group(1).strip()
        # Clean up double spaces, trailing words
        name = re.sub(r'\s+', ' ', name)
        name = re.sub(r'(?i)^(?:footem\s+in|epicsports|rd9sports|worldcup|90live)\s+', '', name).strip()
        return name
    return None

def get_clean_match_title(label, url):
    if not url:
        return None
    # 1. Try to extract from label
    match_name = extract_match_name(label)
    if match_name:
        return match_name
        
    # 2. Try to extract from URL (path)
    parsed = urlparse(url)
    path_segment = parsed.path
    if path_segment.lower().endswith(".html"):
        path_segment = path_segment[:-5]
    elif path_segment.lower().endswith(".htm"):
        path_segment = path_segment[:-4]
        
    path_segment = path_segment.replace("-", " ").replace("_", " ")
    match_name = extract_match_name(path_segment)
    if match_name:
        return match_name.title()
        
    # 3. Fall back
    return None

def is_likely_stream_button(text, url, parent_url):
    u = url.lower()
    t = text.lower()
    
    # Exclude image, style, script, document, font extensions
    ignored_extensions = {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
        ".css", ".woff", ".woff2", ".ttf", ".eot", ".otf",
        ".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z", ".tar", ".gz",
        ".js", ".json"
    }
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in ignored_extensions):
        return False
        
    # Exclude social/template links
    if any(social in u for social in ["whatsapp.com", "t.me", "telegram.me", "facebook.com", "twitter.com", "instagram.com", "pinterest.com", "linkedin.com", "tumblr.com", "blogger.com/profile", "google.com", "themexpose", "gooyaabi"]):
        return False
        
    # Exclude pages like Contact, About, Privacy
    if any(p in u for p in ["/privacy", "/contact", "/about", "/disclaimer", "/terms"]):
        return False
        
    # Exclude typical search query templates
    if "/search?q=" in u:
        return False
        
    # If the URL is a dated post on the same or related blog, it is likely a match preview link, not a stream button
    is_dated_post = bool(re.search(r'/\d{4}/\d{2}/', u))
    if is_dated_post:
        # Check if the text is a short stream button label (e.g. <= 80 chars and has button words)
        is_short_label = len(t) <= 80 and any(kw in t for kw in ["link", "stream", "watch", "live", "play", "channel", "ios", "android"])
        if not is_short_label:
            return False
            
    # The text or URL must contain stream keywords
    keywords = ["link", "stream", "watch", "live", "tv", "channel", "player", "android", "ios", "click", "server", "mirror", "sd", "hd", "play", "quality"]
    if any(k in t for k in keywords):
        return True
        
    # Fallback: if text is empty/generic/short, but the URL has strong stream indicators
    url_keywords = ["link=", "stream", "player", "embed", "channel", "ch=", "watch", "live", "server", "mirror", "play", "quality"]
    if not t.strip() or len(t.strip()) < 5:
        if any(k in u for k in url_keywords):
            return True
            
    return False

def extract_root_links(root_url, headers=None):
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        response = requests.get(root_url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"[-] Error fetching root URL {root_url}: {e}", file=sys.stderr)
        return []

    parser = EpicLinkParser(root_url)
    parser.feed(response.text)
    
    matched_links = []
    seen_urls = set()
    for item in parser.results:
        text = item["text"]
        link_url = item["url"]

        if is_likely_stream_button(text, link_url, root_url) and item["tag"] in ("a", "button"):
            if link_url not in seen_urls:
                matched_links.append({"label": text, "url": link_url})
                seen_urls.add(link_url)

        elif item["tag"] == "iframe":
            # Root page has a direct iframe embed — treat it as a stream candidate
            if link_url and not is_junk_iframe(link_url) and link_url not in seen_urls:
                label = text.strip() or f"Stream {len(matched_links) + 1}"
                matched_links.append({"label": label, "url": link_url})
                seen_urls.add(link_url)
                print(f"  [iframe] Root-level direct embed: {link_url[:80]}")

    return matched_links

def analyze_page(url, html, visited):
    stream_info = {
        "url": url,
        "type": "unknown",
        "streams": [],
        "clear_keys": {},
        "player": "unknown",
        "nested_links": []
    }
    
    # Extract streams from query parameters of the current page URL (e.g. ?url=https://...)
    parsed_url = urlparse(url)
    q_params = parse_qs(parsed_url.query)
    for q_name, q_vals in q_params.items():
        for val in q_vals:
            if val.startswith("http") and (".m3u8" in val.lower() or ".mpd" in val.lower()):
                if val not in stream_info["streams"]:
                    stream_info["streams"].append(val)
                    if ".m3u8" in val.lower() and stream_info["type"] == "unknown":
                        stream_info["type"] = "hls"
                        stream_info["player"] = "hls.js / video.js"
                    elif ".mpd" in val.lower() and stream_info["type"] == "unknown":
                        stream_info["type"] = "dash"
                        stream_info["player"] = "shaka"
                        
    # Detect target channel/stream ID from query parameters to isolate config block
    id_val = None
    for q_name in ["id", "ch", "channel", "stream", "link", "s"]:
        if q_name in q_params:
            id_val = q_params[q_name][0]
            break
            
    search_html = html
    if id_val:
        # Locate the javascript object block containing the channel ID/slug
        def extract_js_object_containing(source, target_value):
            pattern = rf"[\x27\"]{re.escape(target_value)}[\x27\"]"
            match = re.search(pattern, source)
            if match:
                pos = match.start()
            else:
                pos = source.find(target_value)
            if pos == -1:
                return None
                
            start_pos = -1
            brace_count = 0
            for idx in range(pos, -1, -1):
                if source[idx] == '}':
                    brace_count -= 1
                elif source[idx] == '{':
                    brace_count += 1
                    if brace_count == 1:
                        start_pos = idx
                        break
            if start_pos == -1:
                return None
                
            end_pos = -1
            brace_count = 0
            for idx in range(start_pos, len(source)):
                if source[idx] == '{':
                    brace_count += 1
                elif source[idx] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = idx + 1
                        break
            if end_pos == -1:
                return None
            return source[start_pos:end_pos]
            
        block = extract_js_object_containing(html, id_val)
        if block:
            search_html = block

    # ── Hello Sports / lordatomic obfuscated player decoder ──
    # These embeds use a 3-layer encoding: hex → base64 → URL-encode
    # to hide a Shaka Player DASH stream URL + ClearKey DRM pair.
    # Detect the pattern and decode it so existing extractors can find the stream.
    hellosport_hex = re.search(r"'(4a5449[0-9a-fA-F]{500,})'", search_html)
    if hellosport_hex:
        try:
            import urllib.parse as _ulp
            _hs_stage1 = bytes.fromhex(hellosport_hex.group(1)).decode("utf-8", errors="replace")
            _hs_stage2 = base64.b64decode(_hs_stage1).decode("utf-8", errors="replace")
            _hs_decoded = _ulp.unquote(_hs_stage2)
            if ".mpd" in _hs_decoded or "clearKey" in _hs_decoded:
                search_html += "\n" + _hs_decoded
                print(f"[+] Decoded HelloSports obfuscated player ({len(_hs_decoded)} chars)")
        except Exception as _hs_err:
            print(f"[-] HelloSports decode failed: {_hs_err}", file=sys.stderr)

    m3u8_links = re.findall(r"[\x27\"](https?://[^\x27\"]+\.m3u8[^\x27\"]*)[\x27\"]", search_html, re.IGNORECASE)
    mpd_links = re.findall(r"[\x27\"](https?://[^\x27\"]+\.mpd[^\x27\"]*)[\x27\"]", search_html, re.IGNORECASE)
    
    keys_match = re.search(r"clearKeys\s*:\s*\{([^}]+)\}", search_html, re.DOTALL)
    if keys_match:
        pairs = re.findall(r"[\x27\"]([0-9a-fA-F]{32})[\x27\"]\s*:\s*[\x27\"]([0-9a-fA-F]{32})[\x27\"]", keys_match.group(1))
        if pairs:
            stream_info["clear_keys"] = dict(pairs)
            stream_info["player"] = "shaka"
            stream_info["type"] = "dash"
            
    ck_match = re.search(r"[\x27\"]?clearkey[\x27\"]?\s*:\s*[\x27\"]([0-9a-fA-F]{32}):([0-9a-fA-F]{32})[\x27\"]", search_html, re.IGNORECASE)
    if ck_match:
        stream_info["clear_keys"] = {ck_match.group(1): ck_match.group(2)}
        stream_info["player"] = "shaka"
        stream_info["type"] = "dash"

    # Fallback: separate keyId / key variables (Hello Sports pattern)
    if not stream_info.get("clear_keys"):
        kid_match = re.search(r'(?:const|var|let)\s+keyId\s*=\s*[\x27"]([0-9a-fA-F]{32})[\x27"]', search_html)
        key_match = re.search(r'(?:const|var|let)\s+key\b\s*=\s*[\x27"]([0-9a-fA-F]{32})[\x27"]', search_html)
        if kid_match and key_match:
            stream_info["clear_keys"] = {kid_match.group(1): key_match.group(1)}
            stream_info["player"] = "shaka"
            stream_info["type"] = "dash"
        
    jw_match = re.search(r"jwplayer\(.*?\)\.setup\(\{(.*?)\}\)", search_html, re.DOTALL | re.IGNORECASE)
    if jw_match:
        stream_info["player"] = "jwplayer"
        file_match = re.search(r"file\s*:\s*[\x27\"]([^\x27\"]+)[\x27\"]", jw_match.group(1))
        if file_match:
            jw_file = file_match.group(1)
            stream_info["streams"].append(jw_file)
            if ".m3u8" in jw_file.lower():
                stream_info["type"] = "hls"
            elif ".mpd" in jw_file.lower():
                stream_info["type"] = "dash"
                
    for link in m3u8_links:
        parsed_link = urlparse(link)
        link_path = parsed_link.path.lower()
        if any(link_path.endswith(ext) for ext in [".html", ".htm", ".php", ".jsp", ".asp"]):
            continue
        if ".js" not in link.lower() and link not in stream_info["streams"]:
            stream_info["streams"].append(link)
            if stream_info["type"] == "unknown":
                stream_info["type"] = "hls"
                stream_info["player"] = "hls.js / video.js"

    for link in mpd_links:
        parsed_link = urlparse(link)
        link_path = parsed_link.path.lower()
        if any(link_path.endswith(ext) for ext in [".html", ".htm", ".php", ".jsp", ".asp"]):
            continue
        if ".js" not in link.lower() and link not in stream_info["streams"]:
            stream_info["streams"].append(link)
            if stream_info["type"] == "unknown":
                stream_info["type"] = "dash"
                if stream_info["player"] == "unknown":
                    stream_info["player"] = "shaka"

    parser = EpicLinkParser(url)
    parser.feed(html)
    
    iframes = [item["url"] for item in parser.results if item["tag"] == "iframe"]
    # Collect source URLs to prevent self-embedding
    source_portal_urls = set()
    if visited:
        source_portal_urls = set(visited)
    for iframe_url in iframes:
        if iframe_url not in visited and iframe_url != url:
            if is_junk_iframe(iframe_url, source_portal_urls):
                print(f"[*] Filtered junk iframe: {iframe_url[:80]}")
                continue
            stream_info["nested_links"].append({
                "type": "iframe",
                "url": iframe_url
            })
            
    for item in parser.results:
        if item["tag"] in ("a", "button"):
            text = item["text"]
            link_url = item["url"]
            if is_likely_stream_button(text, link_url, url) and link_url not in visited and link_url != url:
                stream_info["nested_links"].append({
                    "type": "iframe",
                    "url": link_url
                })
            
    map_match = re.search(r"(?:const|let|var)?\s*streams\s*=\s*\{([^}]+)\}", html, re.DOTALL)
    if map_match:
        pairs = re.findall(r"[\x27\"]([^\x27\"]+)[\x27\"]\s*:\s*[\x27\"]([^\x27\"]+)[\x27\"]", map_match.group(1))
        stream_map = dict(pairs)
        
        parsed_url = urlparse(url)
        q_params = parse_qs(parsed_url.query)
        id_val = q_params.get("id", [None])[0]
        
        if id_val and id_val in stream_map:
            target_url = stream_map[id_val]
            if target_url and target_url != "#" and target_url not in visited:
                stream_info["nested_links"].append({
                    "type": "js_map_redirect",
                    "url": target_url
                })
        else:
            for k, target_url in stream_map.items():
                if target_url and target_url != "#" and target_url not in visited:
                    stream_info["nested_links"].append({
                        "type": f"js_map_{k}",
                        "url": target_url
                    })
                    
    return stream_info

def crawl_url_recursive(url, depth=0, max_depth=4, visited=None, headers=None, domain_health=None):
    if visited is None:
        visited = set()
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if domain_health is None:
        domain_health = {}
        
    if url in visited:
        return None
    visited.add(url)
    
    if depth > max_depth:
        return None
        
    use_browser = should_use_browser(url) and depth <= 1  # Use browser at L0 and L1 portal pages
    method_label = "browser" if use_browser else "requests"
    print(f"{'  ' * depth}[*] Crawling page ({method_label}): {url}")

    html, success, method = fetch_page_html(url, headers=headers, use_browser=use_browser, timeout=12)
    if not success or not html:
        record_domain_result(domain_health, url, False)
        print(f"{'  ' * depth}[-] Failed to fetch {url} via {method}", file=sys.stderr)
        return {
            "url": url,
            "error": f"Fetch failed via {method}",
            "fetch_method": method,
            "nested_results": []
        }

    record_domain_result(domain_health, url, True)
        
    info = analyze_page(url, html, visited)
    info["nested_results"] = []
    info["fetch_method"] = method
    
    has_redirect = any(item["type"] == "js_map_redirect" for item in info["nested_links"])
    
    for nested in info["nested_links"]:
        should_follow = False
        if nested["type"] == "iframe":
            should_follow = True
        elif nested["type"] == "js_map_redirect":
            should_follow = True
        elif not has_redirect and nested["type"].startswith("js_map_"):
            should_follow = True
            
        if should_follow:
            nested_res = crawl_url_recursive(nested["url"], depth+1, max_depth, visited, headers, domain_health)
            if nested_res:
                info["nested_results"].append(nested_res)
                
    return info

def extract_final_stream_details(tree):
    if not tree:
        return None
        
    streams = list(tree.get("streams", []))
    clear_keys = dict(tree.get("clear_keys", {}))
    player = tree.get("player", "unknown")
    stream_type = tree.get("type", "unknown")
    
    for nested in tree.get("nested_results", []):
        nested_details = extract_final_stream_details(nested)
        if nested_details:
            streams.extend(nested_details["streams"])
            clear_keys.update(nested_details["clear_keys"])
            if player == "unknown" and nested_details["player"] != "unknown":
                player = nested_details["player"]
            if stream_type == "unknown" and nested_details["type"] != "unknown":
                stream_type = nested_details["type"]
                
    streams = list(dict.fromkeys(streams))
    
    # Fallback to iframes if no direct streams found
    if not streams:
        def find_all_iframes(node):
            if not node:
                return []
            iframes = []
            for link in node.get("nested_links", []):
                if link.get("type") == "iframe":
                    iframes.append(link.get("url"))
            for nested in node.get("nested_results", []):
                iframes.extend(find_all_iframes(nested))
            return list(dict.fromkeys(iframes))
            
        iframes = find_all_iframes(tree)
        if iframes:
            # Try to unwrap relay/hub pages to find real streams inside
            unwrapped = _unwrap_iframe_relay_pages(iframes)
            if unwrapped:
                streams = unwrapped
                # Re-detect stream type from unwrapped URLs
                for s in streams:
                    st = infer_stream_type_from_url(s)
                    if st in ("hls", "dash", "native"):
                        stream_type = st
                        player = {"hls": "hls.js", "dash": "shaka", "native": "html5"}.get(st, "unknown")
                        break
                else:
                    stream_type = "iframe"
                    player = "iframe"
            else:
                streams = iframes
                stream_type = "iframe"
                player = "iframe"
            
    return {
        "streams": streams,
        "clear_keys": clear_keys,
        "player": player,
        "type": stream_type
    }


def _unwrap_iframe_relay_pages(iframe_urls):
    """Fetch Blogger relay pages and extract the real stream URLs from inside them.
    
    Relay pages like 'enjoy-live-match-2.html' or 'scroll-down-and-watch-live.html'
    contain inner iframes that point to actual stream players with ?url=, ?b4x= params
    containing m3u8/mpd URLs.
    """
    relay_indicators = [
        "enjoy-live-match", "scroll-down", "watch-live", "live-match",
        "/p/", "blogspot.com",
    ]
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    unwrapped = []
    seen = set()
    
    for iframe_url in iframe_urls:
        url_lower = iframe_url.lower()
        # Only attempt unwrapping for URLs that look like relay/hub pages
        is_relay = any(ind in url_lower for ind in relay_indicators)
        if not is_relay:
            # Keep non-relay iframes as-is (albaplayer, embed URLs, etc.)
            if iframe_url not in seen:
                unwrapped.append(iframe_url)
                seen.add(iframe_url)
            continue
        
        try:
            r = requests.get(iframe_url, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            html = r.text
            
            # Extract inner iframes from the relay page
            inner_iframes = re.findall(
                r'<iframe[^>]+src=["\x27]([^"\x27]+)["\x27]', html, re.IGNORECASE
            )
            
            for inner_url in inner_iframes:
                if not inner_url.startswith("http"):
                    inner_url = urljoin(iframe_url, inner_url)
                
                # Skip junk iframes (youtube placeholder, vimeo placeholder, cbox, etc.)
                if is_junk_iframe(inner_url):
                    continue
                # Skip placeholder/empty embeds and chat widgets
                inner_lower = inner_url.lower()
                if any(p in inner_lower for p in [
                    "cbox.ws", "disqus.com", "facebook.com/plugins",
                ]):
                    continue
                # Skip embeds with empty IDs (placeholder templates like youtube.com/embed/ or vimeo.com/video/)
                path_stripped = urlparse(inner_url).path.rstrip("/")
                if path_stripped in ("/embed", "/video") or not path_stripped:
                    continue
                
                # Try to extract embedded stream URL from query params (?url=, ?b4x=, etc.)
                embedded = extract_embedded_stream_url(inner_url)
                if embedded and embedded not in seen:
                    unwrapped.append(embedded)
                    seen.add(embedded)
                    inner_type = infer_stream_type_from_url(embedded)
                    print(f"  [+] Unwrapped relay iframe: {inner_type.upper()} stream from {urlparse(iframe_url).netloc}")
                elif inner_url not in seen:
                    # If we can't extract a direct stream, keep the inner iframe
                    # (it's still better than the outer relay page)
                    unwrapped.append(inner_url)
                    seen.add(inner_url)
                    print(f"  [+] Unwrapped relay: inner embed from {urlparse(iframe_url).netloc}")
            
            # Also scan for direct m3u8/mpd URLs in the page JS/HTML
            direct_streams = re.findall(
                r'["\x27](https?://[^"\x27\s]+\.(?:m3u8|mpd)[^"\x27\s]*)["\x27]',
                html, re.IGNORECASE
            )
            for stream_url in direct_streams:
                if stream_url not in seen:
                    unwrapped.append(stream_url)
                    seen.add(stream_url)
                    print(f"  [+] Unwrapped relay: direct stream URL from {urlparse(iframe_url).netloc}")
                    
        except Exception as e:
            print(f"  [-] Failed to unwrap relay {iframe_url[:60]}: {e}")
            continue
    
    return unwrapped if unwrapped else None


def parse_manifest_quality(text, stream_type):
    quality = {"height": None, "bandwidth": None}
    if not text:
        return quality

    if stream_type == "hls":
        heights = [int(v) for v in re.findall(r"RESOLUTION=\d+x(\d+)", text, re.IGNORECASE)]
        bandwidths = [int(v) for v in re.findall(r"BANDWIDTH=(\d+)", text, re.IGNORECASE)]
        if heights:
            quality["height"] = max(heights)
        if bandwidths:
            quality["bandwidth"] = max(bandwidths)
    elif stream_type == "dash":
        heights = [int(v) for v in re.findall(r"\bheight=[\"'](\d+)[\"']", text, re.IGNORECASE)]
        bandwidths = [int(v) for v in re.findall(r"\bbandwidth=[\"'](\d+)[\"']", text, re.IGNORECASE)]
        if heights:
            quality["height"] = max(heights)
        if bandwidths:
            quality["bandwidth"] = max(bandwidths)
    return quality


def score_stream_probe(stream_type, latency_ms, height=None, bandwidth=None, status_code=None, url=None, domain_health=None):
    base = {
        "dash": 360,
        "hls": 320,
        "native": 260,
        "iframe": 190,
    }.get(stream_type, 120)

    if height:
        if height >= 1080:
            base += 80
        elif height >= 720:
            base += 55
        elif height >= 480:
            base += 25
    elif bandwidth:
        if bandwidth >= 5000000:
            base += 70
        elif bandwidth >= 2500000:
            base += 45
        elif bandwidth >= 1000000:
            base += 20

    if stream_type == "native":
        base += 25  # smartphone-friendly tie breaker for plain video files
    if status_code in (301, 302, 307, 308):
        base -= 10
    if latency_ms is not None:
        base -= min(int(latency_ms / 100), 60)

    if url:
        url_lower = url.lower()
        # Heavy penalty for Amazon Prime Video live streams which are notoriously geoblocked/unstable for general public
        if any(p in url_lower for p in ["pv-cdn.net", "aiv-cdn.net", "aiv-delivery.net"]):
            base -= 250

        # Apply domain health history penalty
        if domain_health:
            domain = urlparse(url).netloc
            if domain:
                entry = domain_health.get(domain) or domain_health.get(domain.replace("www.", ""))
                if entry:
                    fails = entry.get("fail_count", 0)
                    successes = entry.get("success_count", 0)
                    total = fails + successes
                    if total >= 5:
                        fail_rate = fails / total
                        # Subtract up to 150 points for bad domains
                        penalty = int(fail_rate * 150)
                        if penalty > 0:
                            base -= penalty
                            print(f"[Health Penalty] Domain {domain} has fail rate {fail_rate:.1%} (fails: {fails}, total: {total}). Applied -{penalty} penalty. New score: {base}")

    return base


def infer_stream_type_from_url(url):
    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    url_lower = url.lower()
    if path_lower.endswith(".mpd") or "manifest.mpd" in path_lower or "/dash/" in path_lower:
        return "dash"
    if path_lower.endswith(".m3u8") or "playlist.m3u8" in path_lower or "/hls/" in path_lower:
        return "hls"
    if any(path_lower.endswith(ext) for ext in [".mp4", ".webm", ".ogg", ".ts", ".mkv"]):
        return "native"
    # Only classify as iframe if the URL has strong embed/player indicators.
    # This prevents raw website pages from being embedded in the player.
    embed_indicators = [
        "/embed", "/player", "albaplayer", "/ch",
        "youtube.com", "dailymotion.com", "twitch.tv", "vimeo.com",
        "streamable.com", "ok.ru", "rutube.ru", "odysee.com",
    ]
    query_lower = parsed.query.lower()
    # Check for embed indicators in path or domain
    if any(ind in url_lower for ind in embed_indicators):
        return "iframe"
    # Check for stream-related query parameters that suggest an embed wrapper
    embed_query_keys = {"src", "url", "file", "dtv", "hls", "mpd", "source", "embed", "stream"}
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    for key in query_params:
        if key.lower() in embed_query_keys:
            return "iframe"
    return "iframe"


def extract_embedded_stream_url(url, allow_iframe_candidate=True, _depth=0):
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    preferred_keys = {
        "src", "url", "file", "dtv", "hls", "mpd", "get", "b4x", "source",
        "vivo", "embed", "player", "stream", "xy9ert8", "u",
    }

    def normalize_candidate(value):
        value = unquote(str(value or "").strip())
        if value.startswith(("http://", "https://")):
            return value
        try:
            padded = value + "=" * (-len(value) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="ignore").strip()
            if decoded.startswith(("http://", "https://")):
                return decoded
        except Exception:
            pass
        return ""

    def resolve_candidate(candidate, preferred=False):
        if not candidate:
            return ""
        candidate_type = infer_stream_type_from_url(candidate)
        if candidate_type in ("hls", "dash", "native"):
            return candidate
        if _depth < 2:
            nested = extract_embedded_stream_url(candidate, allow_iframe_candidate, _depth + 1)
            if nested:
                return nested
        if preferred and allow_iframe_candidate and candidate_type == "iframe":
            return candidate
        return ""

    for key, values in params.items():
        if key.lower() not in preferred_keys:
            continue
        for value in values:
            candidate = normalize_candidate(value)
            resolved = resolve_candidate(candidate, preferred=True)
            if resolved:
                return resolved
    for values in params.values():
        for value in values:
            candidate = normalize_candidate(value)
            resolved = resolve_candidate(candidate, preferred=False)
            if resolved and infer_stream_type_from_url(resolved) in ("hls", "dash", "native"):
                return resolved
    return ""


def response_cors_ok(response):
    origin_header = response.headers.get("access-control-allow-origin", "").strip().lower()
    if not origin_header:
        return False
    origins = {o.strip() for o in origin_header.split(",")}
    if "*" in origins:
        return True
    allowed = {
        "https://qtwc2022.blogspot.com",
        "https://www.goforsports.net",
        "https://goforsports.net",
    }
    for o in origins:
        if o in allowed or "blogspot.com" in o or "goforsports.net" in o:
            return True
    return False


def read_stream_text(response, max_bytes=200000):
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    return b"".join(chunks).decode("utf-8", errors="ignore")


def first_playlist_uri(manifest_text):
    for line in manifest_text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def playlist_uris(manifest_text):
    uris = []
    for line in manifest_text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            uris.append(line)
    return uris


def is_vod_or_finished_hls(manifest_text):
    upper = (manifest_text or "").upper()
    return "#EXT-X-PLAYLIST-TYPE:VOD" in upper or "#EXT-X-ENDLIST" in upper


def response_looks_like_html(content_bytes, content_type=""):
    if "text/html" in (content_type or "").lower():
        return True
    sample = (content_bytes or b"").lstrip()[:256].lower()
    return sample.startswith((b"<!doctype html", b"<html", b"<script"))


def probe_hls_segment(segment_url, headers):
    response = requests.get(segment_url, headers=headers, timeout=8, stream=True, allow_redirects=True)
    status_code = response.status_code
    cors_ok = response_cors_ok(response)
    content_type = response.headers.get("content-type", "")
    sample = b""
    try:
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                sample += chunk
            if len(sample) >= 2048:
                break
    finally:
        response.close()

    if status_code not in (200, 206):
        return False, f"hls-segment-http-{status_code}", status_code
    if not cors_ok:
        return False, "hls-segment-cors-blocked", status_code
    if not sample:
        return False, "hls-segment-empty", status_code
    if response_looks_like_html(sample, content_type):
        return False, "hls-segment-html", status_code
    return True, "hls-media-ok", status_code


def probe_hls_media_playlist(playlist_url, playlist_text, headers):
    if is_vod_or_finished_hls(playlist_text):
        return False, "hls-vod-or-ended-playlist", None
    if "#EXTINF" not in playlist_text and "#EXT-X-MAP" not in playlist_text:
        return False, "hls-no-media-segments", None

    failures = []
    for media_uri in playlist_uris(playlist_text)[:5]:
        if media_uri.lower().split("?", 1)[0].endswith(".m3u8"):
            continue
        media_url = urljoin(playlist_url, media_uri)
        ok, reason, status = probe_hls_segment(media_url, headers)
        if ok:
            return True, reason, status
        failures.append(reason)
    return False, failures[-1] if failures else "hls-no-media-uri", None


def probe_hls_media(manifest_url, manifest_text, headers):
    if is_vod_or_finished_hls(manifest_text):
        return False, "hls-vod-or-ended-playlist", None

    first_uri = first_playlist_uri(manifest_text)
    if not first_uri:
        return False, "hls-no-media-uri", None

    if "#EXT-X-STREAM-INF" not in manifest_text:
        return probe_hls_media_playlist(manifest_url, manifest_text, headers)

    failures = []
    for variant_uri in playlist_uris(manifest_text)[:5]:
        child_url = urljoin(manifest_url, variant_uri)
        response = requests.get(child_url, headers=headers, timeout=8, stream=True, allow_redirects=True)
        status_code = response.status_code
        cors_ok = response_cors_ok(response)
        if response.status_code not in (200, 206):
            response.close()
            failures.append(f"hls-child-http-{status_code}")
            continue
        if not cors_ok:
            response.close()
            failures.append("hls-child-cors-blocked")
            continue

        content = read_stream_text(response, max_bytes=65536)
        response.close()
        if not content.lstrip().startswith("#EXTM3U"):
            failures.append("hls-child-invalid-manifest")
            continue
        media_ok, media_reason, media_status = probe_hls_media_playlist(child_url, content, headers)
        if media_ok:
            return True, media_reason, media_status
        failures.append(media_reason)

    return False, failures[-1] if failures else "hls-no-playable-variant", None


def dash_manifest_kids(manifest_text):
    kids = set()
    patterns = [
        r"default_KID=[\"']([0-9a-fA-F-]{32,36})[\"']",
        r"cenc:default_KID=[\"']([0-9a-fA-F-]{32,36})[\"']",
    ]
    for pattern in patterns:
        kids.update(value.lower().replace("-", "") for value in re.findall(pattern, manifest_text))
    return kids


def probe_dash_init_segment(manifest_url, manifest_bytes, headers):
    try:
        root = ET.fromstring(manifest_bytes)
    except Exception:
        return False, "dash-xml-parse-failed", None

    representations = root.findall(".//{*}Representation")
    templates = root.findall(".//{*}SegmentTemplate")
    if not representations or not templates:
        return True, "dash-manifest-ok", None

    representation = representations[0]
    template = templates[0]
    init_template = template.attrib.get("initialization", "")
    if not init_template:
        return True, "dash-manifest-ok", None

    base_url = ""
    base_el = root.find(".//{*}BaseURL")
    if base_el is not None and base_el.text:
        base_url = base_el.text.strip()

    init_path = (
        init_template
        .replace("$RepresentationID$", representation.attrib.get("id", ""))
        .replace("$Bandwidth$", representation.attrib.get("bandwidth", ""))
    )
    init_url = urljoin(manifest_url, urljoin(base_url, init_path))
    response = requests.get(init_url, headers=headers, timeout=6, stream=True, allow_redirects=True)
    status_code = response.status_code
    cors_ok = response_cors_ok(response)
    response.close()
    if status_code not in (200, 206):
        return False, f"dash-init-http-{status_code}", status_code
    if not cors_ok:
        return False, "dash-init-cors-blocked", status_code
    return True, "dash-init-ok", status_code


def probe_stream_url(url, stream_type, clear_keys=None, domain_health=None):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://qtwc2022.blogspot.com/",
        "Origin": "https://qtwc2022.blogspot.com",
    }
    result = {
        "working": False,
        "status_code": None,
        "latency_ms": None,
        "height": None,
        "bandwidth": None,
        "score": 0,
        "error": "",
        "validation_status": "failed",
        "validation_reason": "",
        "content_type": "",
        "media_probe_status": None,
        "cors_ok": None,
        "backup": False,
        "is_vod": False,
    }
    if not url.startswith("http"):
        result["error"] = "non-http-url"
        result["validation_reason"] = "non-http-url"
        return result

    # Blocklist check for known non-match/local stream patterns
    url_lower = url.lower()
    blocked_patterns = ["puertorico", "nbculocallive.akamaized.net"]
    for pattern in blocked_patterns:
        if pattern in url_lower:
            result["error"] = "blocked-stream-pattern"
            result["validation_reason"] = f"url-contains-blocked-pattern:{pattern}"
            return result
    # Reject junk iframes at probe level as an extra safety net
    if stream_type == "iframe" and is_junk_iframe(url):
        result["error"] = "junk-iframe"
        result["validation_reason"] = "junk-iframe-blocked"
        return result
    r = None
    try:
        started = time.monotonic()
        r = requests.get(url, headers=headers, timeout=6, stream=True, allow_redirects=True)
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["status_code"] = r.status_code
        result["content_type"] = r.headers.get("content-type", "")
        result["cors_ok"] = response_cors_ok(r)
        ok_statuses = (200, 206, 301, 302, 307, 308)
        if stream_type == "iframe":
            ok_statuses = ok_statuses + (401, 403)
        if r.status_code not in ok_statuses:
            result["error"] = f"http-{r.status_code}"
            result["validation_reason"] = result["error"]
            print(f"[-] Stream URL validation failed for {url} with status {r.status_code}")
            return result
        if stream_type == "hls" and not result["cors_ok"]:
            result["error"] = "hls-manifest-cors-blocked"
            result["validation_reason"] = result["error"]
            return result
        if stream_type == "dash" and not result["cors_ok"]:
            result["error"] = "dash-manifest-cors-blocked"
            result["validation_reason"] = result["error"]
            return result

        manifest_text = ""
        manifest_bytes = b""
        if stream_type in ("hls", "dash"):
            manifest_chunks = []
            manifest_size = 0
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    manifest_chunks.append(chunk)
                    manifest_size += len(chunk)
                if manifest_size >= 1000000:
                    break
            manifest_bytes = b"".join(manifest_chunks)
            manifest_text = manifest_bytes[:200000].decode("utf-8", errors="ignore")
        elif stream_type == "iframe":
            iframe_html = read_stream_text(r, max_bytes=100000)
            iframe_lower = iframe_html.lower()
            offline_patterns = [
                "stream offline",
                "stream is offline",
                "currently offline",
                "channel offline",
                "stream not found",
                "no stream",
                "error loading stream",
                "loading failed",
                "stream ended",
                "match ended",
                "invalid stream",
                "access denied",
                "geo block",
                "not allowed in your country",
                "not available in your region",
            ]
            for pat in offline_patterns:
                if pat in iframe_lower:
                    result["error"] = f"iframe-offline-content:{pat.replace(' ', '-')}"
                    result["validation_reason"] = result["error"]
                    return result
            if len(iframe_html.strip()) < 150 and "iframe" not in iframe_lower and "embed" not in iframe_lower and "video" not in iframe_lower:
                result["error"] = "iframe-content-too-short"
                result["validation_reason"] = result["error"]
                return result

            # Strong player signals: actual HTML player elements, known player libraries,
            # or media file references. Generic words like "player", "stream", "live" are
            # NOT strong signals — every sports website has those.
            strong_player_signals = [
                "<video", "<iframe", "<embed", "<object",
                "jwplayer", "flowplayer", "videojs", "clappr",
                "hls.js", "dash.js", "shakaplayer", "plyr",
                ".m3u8", ".mpd", ".mp4", "wmsauthsign",
                "mediaelement", "bitmovin", "theoplayer",
                "new hls(", "new shaka", "new dashjs",
                "createplayer", "initplayer", "loadplayer",
                "playsinline", "autoplay",
            ]
            if not any(sig in iframe_lower for sig in strong_player_signals):
                result["error"] = "no-player-signals-in-iframe"
                result["validation_reason"] = result["error"]
                return result

            # Detect raw website pages that have player elements but are full websites
            # (navigation bars, multiple articles, sidebars, etc.) — not embeddable players
            raw_website_signals = [
                "<nav", "<header", "<footer", "<aside",
                "class=\"sidebar", "class=\"navbar", "class=\"menu",
                "class=\"article", "class=\"post-body",
                "class=\"widget", "class=\"blog-post",
            ]
            raw_signal_count = sum(1 for sig in raw_website_signals if sig in iframe_lower)
            # If the page has 3+ raw website signals, it's likely a full website, not an embed
            if raw_signal_count >= 3:
                result["error"] = "iframe-is-raw-website"
                result["validation_reason"] = f"raw-website-signals:{raw_signal_count}"
                return result

        if stream_type == "hls":
            if not manifest_text.lstrip().startswith("#EXTM3U"):
                result["error"] = "html-instead-of-hls-manifest"
                result["validation_reason"] = result["error"]
                return result
            media_ok, media_reason, media_status = probe_hls_media(r.url, manifest_text, headers)
            result["media_probe_status"] = media_status
            if not media_ok:
                result["error"] = media_reason
                result["validation_reason"] = media_reason
                return result
            result["validation_reason"] = media_reason
        elif stream_type == "dash":
            if "<MPD" not in manifest_text:
                result["error"] = "invalid-dash-manifest"
                result["validation_reason"] = result["error"]
                return result
            manifest_kids = dash_manifest_kids(manifest_text)
            provided_keys = set((clear_keys or {}).keys())
            missing_keys = manifest_kids - provided_keys
            if missing_keys:
                result["error"] = "drm-keys-missing"
                result["validation_reason"] = f"missing-keys:{','.join(missing_keys)}"
                return result
            init_ok, init_reason, init_status = probe_dash_init_segment(r.url, manifest_bytes, headers)
            result["media_probe_status"] = init_status
            if not init_ok:
                result["error"] = init_reason
                result["validation_reason"] = init_reason
                return result
            if not result["validation_reason"]:
                result["validation_reason"] = init_reason

        quality = parse_manifest_quality(manifest_text, stream_type)
        result.update(quality)
        result["working"] = True
        result["validation_status"] = "ok"
        result["score"] = score_stream_probe(
            stream_type,
            result["latency_ms"],
            height=result["height"],
            bandwidth=result["bandwidth"],
            status_code=result["status_code"],
            url=url,
            domain_health=domain_health,
        )
        if result["backup"]:
            result["score"] -= 180
        return result
    except Exception as e:
        result["error"] = str(e)
        result["validation_reason"] = str(e)
        print(f"[-] Stream URL validation failed for {url} with exception: {e}")
        return result
    finally:
        if r is not None:
            r.close()


def is_stream_url_working(url, stream_type):
    return probe_stream_url(url, stream_type).get("working", False)

def process_root_url(root_url, max_depth=4, domain_health=None):
    if domain_health is None:
        domain_health = {}

    print(f"\n[*] STEP 1: Scraping page for stream links: {root_url}")

    # Use browser for the root page if it's a JS-heavy domain
    use_browser_for_root = should_use_browser(root_url)
    if use_browser_for_root:
        html, success, method = fetch_page_html(root_url, use_browser=True, timeout=15)
        if success and html:
            record_domain_result(domain_health, root_url, True)
            # Parse the browser-rendered HTML for stream buttons
            parser = EpicLinkParser(root_url)
            parser.feed(html)
            matched_links = []
            for item in parser.results:
                if item["tag"] in ("a", "button") and is_likely_stream_button(item["text"], item["url"], root_url):
                    matched_links.append({"label": item["text"], "url": item["url"]})
        else:
            record_domain_result(domain_health, root_url, False)
            matched_links = extract_root_links(root_url)
    else:
        matched_links = extract_root_links(root_url)

    print(f"[+] Found {len(matched_links)} stream button(s)/link(s).")
    
    results = []
    for idx, item in enumerate(matched_links, 1):
        print(f"\n[*] STEP 2: Crawling button {idx}/{len(matched_links)}: {item['label']}")
        print(f"[*] Target URL: {item['url']}")
        
        visited = set()
        tree = crawl_url_recursive(item["url"], depth=0, max_depth=max_depth, visited=visited, domain_health=domain_health)
        details = extract_final_stream_details(tree)
        
        results.append({
            "label": item["label"],
            "root_target_url": item["url"],
            "root_origin_url": root_url,
            "details": details
        })
        
    return results

# ----------------------------------------------------------------------
# Main Builder Execution
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Scrapes live streaming root URLs, extracts links, crawls subpages, and generates a fully updated HTML Player."
    )
    parser.add_argument(
        "-u", "--urls",
        nargs="*",
        help="One or more root page URLs to crawl (separated by space)."
    )
    parser.add_argument(
        "-t", "--title",
        help="Canonical match title to use for stream link labeling (e.g. 'Germany Vs Curacao')."
    )
    parser.add_argument(
        "-f", "--file",
        help="Path to a text file containing root URLs (one per line)."
    )
    parser.add_argument(
        "-o", "--output",
        default="player.html",
        help="Path where the final HTML player file should be written (default: player.html)."
    )
    parser.add_argument(
        "-d", "--depth",
        type=int,
        default=4,
        help="Maximum recursion depth for following iframes/redirects (default: 2)."
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously in a daemon/loop mode."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Sleep interval in minutes between pipeline runs in loop mode (default: 5)."
    )
    
    args = parser.parse_args()
    
    # Collect root URLs
    root_urls = []
    if args.urls:
        root_urls.extend(args.urls)
    if args.file:
        if os.path.exists(args.file):
            with open(args.file, "r") as f:
                root_urls.extend([line.strip() for line in f if line.strip() and not line.strip().startswith("#")])
        else:
            print(f"[-] Error: File '{args.file}' not found.", file=sys.stderr)
            sys.exit(1)
            
    if not root_urls:
        root_urls.append("https://www.epicsports.mobi/p/most-expensive-fifa-world-cups-in.html")
        
    print(f"[*] Processing {len(root_urls)} root URL(s)...")
    
    import time
    import html

    # Standalone Embed Player Template
    EMBED_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1">
<title>{title}</title>
<!-- Shaka Player (DASH + HLS native) -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.7.11/shaka-player.compiled.min.js"></script>
<!-- HLS.js fallback -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.4.10/hls.min.js"></script>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { width:100%; height:100%; background:#000; overflow:hidden; font-family:sans-serif; }
  #video-wrapper { width:100%; height:100%; position:relative; display:flex; align-items:center; justify-content:center; }
  video { width:100%; height:100%; object-fit:contain; background:#000; }
  iframe { width:100%; height:100%; border:none; background:#000; }
  .overlay {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    background: rgba(10,10,15,0.9); color: #fff; z-index: 10; font-size: 16px; transition: opacity 0.5s;
  }
  .spinner {
    width: 50px; height: 50px; border: 3px solid rgba(255,255,255,0.1);
    border-radius: 50%; border-top-color: #e63946; animation: spin 1s ease-in-out infinite; margin-bottom: 15px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .hidden { opacity: 0; pointer-events: none; }
</style>
</head>
<body>
<div id="video-wrapper">
  <div id="overlay-load" class="overlay">
    <div class="spinner"></div>
    <div id="load-msg">Loading stream...</div>
  </div>
  <div id="overlay-error" class="overlay hidden">
    <div style="color:#e63946; font-size:24px; margin-bottom:10px;">⚠ Playback Error</div>
    <div id="err-msg">Stream could not be loaded.</div>
  </div>
  <video id="video" controls autoplay playsinline></video>
  <div id="iframe-wrap" style="display:none; width:100%; height:100%;">
    <iframe id="iframe-el"
      allow="autoplay; encrypted-media; picture-in-picture"
      sandbox="allow-scripts allow-same-origin allow-presentation"
      referrerpolicy="no-referrer"
      allowfullscreen></iframe>
  </div>
</div>
<script>
  const streamUrl = {url_json};
  const streamType = {type_json};
  const clearKeys = {keys_json};

  const video = document.getElementById('video');
  const overlayLoad = document.getElementById('overlay-load');
  const overlayError = document.getElementById('overlay-error');
  const loadMsg = document.getElementById('load-msg');
  const errMsg = document.getElementById('err-msg');
  const iframeWrap = document.getElementById('iframe-wrap');
  const iframeEl = document.getElementById('iframe-el');

  let shakaPlayer = null;
  let hlsInstance = null;

  function initPlayer(url, type) {
    if (type === 'iframe') {
      video.style.display = 'none';
      iframeWrap.style.display = 'block';
      iframeEl.src = url;
      overlayLoad.classList.add('hidden');
      return;
    }

    if (type === 'dash') {
      if (shaka.Player.isBrowserSupported()) {
        shakaPlayer = new shaka.Player(video);
        shakaPlayer.addEventListener('error', (e) => {
          console.error("Shaka error", e);
          showError("DASH Player Error: " + e.detail.code);
        });
        
        if (clearKeys && Object.keys(clearKeys).length > 0) {
          shakaPlayer.configure({
            drm: { clearKeys: clearKeys }
          });
        }

        shakaPlayer.load(url).then(() => {
          overlayLoad.classList.add('hidden');
          video.play().catch(()=>{});
        }).catch((e) => {
          console.error("Shaka load error", e);
          showError("Could not load DASH manifest.");
        });
      } else {
        showError("DASH is not supported by this browser.");
      }
    } else if (type === 'hls') {
      if (Hls.isSupported()) {
        hlsInstance = new Hls({ maxMaxBufferLength: 10 });
        hlsInstance.loadSource(url);
        hlsInstance.attachMedia(video);
        hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => {
          overlayLoad.classList.add('hidden');
          video.play().catch(()=>{});
        });
        hlsInstance.on(Hls.Events.ERROR, (event, data) => {
          if (data.fatal) {
            console.error("HLS fatal error", data);
            showError("HLS fatal playback error.");
          }
        });
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = url;
        video.addEventListener('loadedmetadata', () => {
          overlayLoad.classList.add('hidden');
          video.play().catch(()=>{});
        });
        video.addEventListener('error', () => {
          showError("Native HLS playback error.");
        });
      } else {
        showError("HLS is not supported by this browser.");
      }
    } else {
      video.src = url;
      video.addEventListener('loadedmetadata', () => {
        overlayLoad.classList.add('hidden');
        video.play().catch(()=>{});
      });
      video.addEventListener('error', () => {
        showError("Native HTML5 playback error.");
      });
    }
  }

  function showError(msg) {
    overlayLoad.classList.add('hidden');
    errMsg.textContent = msg;
    overlayError.classList.remove('hidden');
  }

  initPlayer(streamUrl, streamType);
</script>
</body>
</html>"""

    # Iframes Index Page Template
    IFRAMES_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Embeddable Player Iframes</title>
<style>
  body {{ font-family: sans-serif; background: #0a0a0f; color: #f0f0f0; padding: 20px; }}
  h1, h2, h3 {{ color: #e63946; }}
  .card {{ background: #111118; border: 1px solid rgba(255,255,255,0.07); padding: 15px; margin-bottom: 20px; border-radius: 8px; }}
  code {{ display: block; background: #000; padding: 10px; border-radius: 4px; border: 1px solid #333; color: #50fa7b; overflow-x: auto; white-space: pre-wrap; word-break: break-all; }}
</style>
</head>
<body>
  <h1>Embeddable Player Iframes</h1>
  <p>Use the following iframe codes to embed the live streams on other websites. The master player automatically updates as matches change.</p>
  
  <div class="card">
    <h2>1. Master Interactive Player</h2>
    <p>This player includes the channel sidebar, auto-failover, and time-based match listings. It automatically updates in real-time as new matches start.</p>
    <code>&lt;iframe src="player.html" width="100%" height="600px" frameborder="0" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen&gt;&lt;/iframe&gt;</code>
  </div>

  <h2>2. Individual Direct Streams</h2>
  <div id="streams-list">
    {stream_iframes}
  </div>
</body>
</html>"""

    while True:
        print(f"\n==================================================")
        print(f"[*] Pipeline iteration started at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"==================================================")
        
        # Clear previously generated files
        import shutil
        output_dir = os.path.dirname(os.path.abspath(args.output))
        embeds_dir = os.path.join(output_dir, "embeds")
        iframes_html_path = os.path.join(output_dir, "iframes.html")
        
        print("[*] Clearing previously generated files...")
        for path in [args.output, iframes_html_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"[-] Warning: could not remove {path}: {e}")
                    
        if os.path.exists(embeds_dir):
            try:
                shutil.rmtree(embeds_dir)
            except Exception as e:
                print(f"[-] Warning: could not remove directory {embeds_dir}: {e}")
        
        expanded_root_urls = []
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

        # Load domain health for prioritization
        domain_health = load_domain_health()
        
        for url in root_urls:
            try:
                parsed_url = urlparse(url)
                path_lower = parsed_url.path.lower()
                # If path contains a dated pattern or ends with .html/.htm or is a static page (/p/), it is a specific match page, not a portal
                is_portal = True
                if re.search(r'/\d{4}/\d{2}/', path_lower) or path_lower.endswith(".html") or path_lower.endswith(".htm") or "/p/" in path_lower:
                    is_portal = False
                
                if is_portal:
                    print(f"[*] Checking if root URL is a match portal: {url}")
                    # Use browser-based fetch for JS-heavy portals
                    html, success, method = fetch_page_html(url, headers=headers, use_browser=should_use_browser(url), timeout=15)
                    if success and html:
                        record_domain_result(domain_health, url, True)
                        match_pages = get_match_pages_from_root(url, html)
                    else:
                        record_domain_result(domain_health, url, False)
                        match_pages = []
                else:
                    match_pages = []
                
                if is_portal and match_pages:
                    print(f"[+] Found {len(match_pages)} total match page(s) on portal. Filtering by time...")
                    active_count = 0
                    for mp in match_pages:
                        time_str = mp.get("time_str")
                        # Check scheduling proximity
                        if is_match_active(time_str):
                            active_count += 1
                            time_lbl = f" [{time_str}]" if time_str else ""
                            print(f"  - ACTIVE: {mp['label']}{time_lbl} -> {mp['url']}")
                            if mp['url'] not in expanded_root_urls:
                                expanded_root_urls.append(mp['url'])
                        else:
                            print(f"  - SKIPPED (not starting soon/active): {mp['label']} [{time_str}] -> {mp['url']}")
                    print(f"[+] Added {active_count} active match page(s) out of {len(match_pages)}.")
                else:
                    print(f"[+] URL is a direct match page or no portal sub-pages found. Processing directly: {url}")
                    if url not in expanded_root_urls:
                        expanded_root_urls.append(url)
            except Exception as e:
                print(f"[-] Warning: Failed to pre-scan root URL {url}: {e}")
                record_domain_result(domain_health, url, False)
                if url not in expanded_root_urls:
                    expanded_root_urls.append(url)

        # Sort URLs by domain health: healthy domains first, failing domains last
        expanded_root_urls = sort_urls_by_domain_health(expanded_root_urls, domain_health)
        print(f"[*] Total target URL(s) to process after time-filtering and health-sorting: {len(expanded_root_urls)}")
        
        # Process and crawl all streams
        all_results = []
        for root_url in expanded_root_urls:
            results = process_root_url(root_url, max_depth=args.depth, domain_health=domain_health)
            all_results.extend(results)

        # Persist updated domain health
        save_domain_health(domain_health)
        
        # Format results to JavaScript objects for STREAM_LINKS with sorting by priority (DASH first, then HLS, etc.)
        resolved_items = []
        seen_urls = set()
        
        for res in all_results:
            label_raw = res["label"]
            details = res["details"]
            
            if not details or not details["streams"]:
                print(f"[-] Skipping {label_raw}: No stream URL resolved.")
                continue
                
            for s_idx, original_stream_url in enumerate(details["streams"], 1):
                playback_url = original_stream_url
                embedded_stream_url = extract_embedded_stream_url(original_stream_url)
                s_type = infer_stream_type_from_url(original_stream_url)
                if s_type == "iframe" and embedded_stream_url:
                    playback_url = embedded_stream_url
                    s_type = infer_stream_type_from_url(playback_url)

                if playback_url in seen_urls:
                    print(f"[-] Skipping duplicate stream URL for: {label_raw} (Stream {s_idx})")
                    continue

                clear_keys_for_probe = details["clear_keys"] if s_type == "dash" else {}

                # Check if the stream link is responsive/playable and collect ranking data.
                probe = probe_stream_url(playback_url, s_type, clear_keys=clear_keys_for_probe, domain_health=domain_health)

                # If inner (extracted) URL failed but original is a wrapper iframe,
                # fall back to the original iframe URL — it handles auth client-side.
                if not probe.get("working") and playback_url != original_stream_url:
                    print(f"[*] Embedded target failed ({probe.get('validation_reason') or probe.get('error')}), falling back to iframe: {original_stream_url[:80]}")
                    playback_url = original_stream_url
                    s_type = "iframe"
                    probe = probe_stream_url(playback_url, s_type, domain_health=domain_health)

                if not probe.get("working"):
                    print(f"[-] Skipping dead/unresponsive stream URL: {playback_url} ({probe.get('validation_reason') or probe.get('error')})")
                    continue

                seen_urls.add(playback_url)
                    
                # Clear keys only for DASH
                s_keys = details["clear_keys"] if s_type == "dash" else {}
                
                # Parse badges from label contents or types
                badges = [s_type]
                label_lower = label_raw.lower()
                if "hd" in label_lower or "hd" in playback_url.lower():
                    badges.append("hd")
                if "sd" in label_lower:
                    badges.append("sd")
                if "eng" in label_lower or "english" in label_lower:
                    badges.append("eng")
                if "ara" in label_lower or "arabic" in label_lower:
                    badges.append("ara")
                if s_type == "hls" or "ios" in label_lower or "ios" in playback_url.lower() or "iphone" in label_lower:
                    badges.append("ios")
                if probe.get("backup"):
                    badges.append("backup")
                    
                badges = list(dict.fromkeys(badges))
                
                # Construct meta descriptive line
                meta_parts = []
                if s_type == "dash":
                    meta_parts.append("MPEG-DASH")
                elif s_type == "hls":
                    meta_parts.append("HLS")
                elif s_type == "iframe":
                    meta_parts.append("HTML5 Embed")
                else:
                    meta_parts.append(s_type.upper())
                    
                meta_parts.append("Auto Quality")

                if probe.get("height"):
                    meta_parts.append(f"{probe['height']}p")
                if probe.get("latency_ms") is not None:
                    meta_parts.append(f"{probe['latency_ms']} ms")
                if probe.get("validation_reason") and probe.get("backup"):
                    meta_parts.append("Backup")
                
                if "eng" in badges:
                    meta_parts.append("English Audio")
                elif "ara" in badges:
                    meta_parts.append("Arabic Audio")
                    
                if s_keys:
                    meta_parts.append("DRM ClearKey Protected")
                    
                resolved_items.append({
                    "label_raw": label_raw,
                    "root_target_url": res["root_target_url"],
                    "root_origin_url": res.get("root_origin_url"),
                    "stream_url": playback_url,
                    "original_stream_url": original_stream_url,
                    "stream_type": s_type,
                    "clear_keys": s_keys,
                    "badges": badges,
                    "meta_parts": meta_parts,
                    "probe": probe,
                    "score": probe.get("score", 0)
                })

        # Sort by playback policy: prioritize stable iframe (embed) and dash streams first
        # to prevent HLS auto-switching issues in the top links.
        def get_type_priority(item):
            if (item.get("probe") or {}).get("backup"):
                return 5
            t = item["stream_type"]
            if t == "iframe":
                return 0
            elif t == "dash":
                return 1
            elif t == "hls":
                return 2
            elif t == "native":
                return 3
            return 4

        resolved_items.sort(key=lambda item: (get_type_priority(item), -int(item.get("score") or 0)))
        resolved_items = resolved_items[:20]

        # Label and build the final STREAM_LINKS array
        stream_links_js = []
        for idx, item in enumerate(resolved_items, 1):
            label_raw = item["label_raw"]
            stream_url = item["stream_url"]
            stream_type = item["stream_type"]
            clear_keys = item["clear_keys"]
            badges = item["badges"]
            meta_parts = item["meta_parts"]
            
            # Clean labels
            match_title = args.title or get_clean_match_title(label_raw, item.get("root_origin_url") or item["root_target_url"])
            if match_title:
                clean_label = f"Link {idx} — {match_title}"
            else:
                if " | " in label_raw:
                    parts = label_raw.split(" | ")
                    if len(parts) >= 2:
                        clean_label = f"Link {idx} — {parts[1]}"
                elif label_raw.lower().startswith("link"):
                    clean_label = re.sub(r"^Link\s*\d+", f"Link {idx}", label_raw, flags=re.IGNORECASE)
                else:
                    clean_label = f"Link {idx} — {label_raw}"
                
            meta_str = " · ".join(meta_parts)
            
            js_obj = {
                "label": clean_label,
                "meta": meta_str,
                "badges": badges,
                "type": stream_type,
                "url": stream_url,
                "score": item.get("score", 0),
                "latencyMs": (item.get("probe") or {}).get("latency_ms"),
                "height": (item.get("probe") or {}).get("height")
            }
            if clear_keys:
                js_obj["clearKeys"] = clear_keys
                
            stream_links_js.append(js_obj)
            
        write_crawl_diagnostics(args.output, expanded_root_urls, all_results, resolved_items)

        # Serialize Python dictionary to JavaScript array format
        js_array_str = "const STREAM_LINKS = " + json.dumps(stream_links_js, indent=2) + ";"
        
        # Inject stream links plus master-configured ads/social/smartlink into the player template.
        output_html = render_player_html(js_array_str)
        
        # Write output HTML file
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output_html)
            print(f"\n[+] SUCCESS: HTML Player generated successfully!")
            print(f"[+] Output written to: {os.path.abspath(args.output)}")
            
            # Automatically generate flat HTML links file in links/ folder
            try:
                base_name = os.path.basename(args.output)
                if base_name.endswith(".html"):
                    base_name = base_name[:-5]
                if base_name.startswith("player_"):
                    base_name = base_name[7:]
                match_name = base_name.replace("_", " ").title()
                
                # Fetch blogger post URL from active schedule if available.
                blogger_url = "https://qtwc2022.blogspot.com/p/world-cup-1.html"
                try:
                    sched = load_schedule(load_automation_config())
                    for m in sched:
                        m_n = m.get("match_name", "").lower().strip()
                        if m_n == match_name.lower().strip() or m_n.replace(" ", "_") == base_name:
                            if m.get("blogger_post_url"):
                                blogger_url = m["blogger_post_url"]
                                break
                except Exception:
                    pass
                    
                from match_scheduler import write_direct_links
                write_direct_links(match_name, blogger_url, output_html, load_automation_config())
            except Exception as e:
                print(f"[-] Warning: Failed to auto-generate direct links list file: {e}")
        except Exception as e:
            print(f"[-] Error writing output HTML file: {e}", file=sys.stderr)
            
        # Generate standalone embeds and iframes.html
        output_dir = os.path.dirname(os.path.abspath(args.output))
        embeds_dir = os.path.join(output_dir, "embeds")
        os.makedirs(embeds_dir, exist_ok=True)
        
        iframe_rows = []
        for idx, item in enumerate(stream_links_js, 1):
            url = item["url"]
            stype = item["type"]
            keys = item.get("clearKeys", {})
            label = item["label"]
            
            # Write individual embed HTML file
            filename = f"embed_{idx}.html"
            filepath = os.path.join(embeds_dir, filename)
            
            content = EMBED_TEMPLATE.replace("{url_json}", json.dumps(url))
            content = content.replace("{type_json}", json.dumps(stype))
            content = content.replace("{keys_json}", json.dumps(keys))
            content = content.replace("{title}", label)
            
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
                
            iframe_tag = f'<iframe src="embeds/embed_{idx}.html" width="100%" height="450px" frameborder="0" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>'
            
            iframe_rows.append(f"""
            <div class="card">
              <h3>{html_escape(label)}</h3>
              <p>Format: {html_escape(item['meta'])}</p>
              <code>{html_escape(iframe_tag)}</code>
            </div>
            """)
            
        # Write iframes.html
        iframes_html_path = os.path.join(output_dir, "iframes.html")
        iframes_content = IFRAMES_PAGE_TEMPLATE.format(stream_iframes="\n".join(iframe_rows))
        try:
            with open(iframes_html_path, "w", encoding="utf-8") as f:
                f.write(iframes_content)
            print(f"[+] SUCCESS: Iframes listing written to: {os.path.abspath(iframes_html_path)}")
        except Exception as e:
            print(f"[-] Error writing iframes listing: {e}", file=sys.stderr)
            
        if not args.loop:
            break
            
        print(f"\n[*] Daemon mode active: sleeping for {args.interval} minute(s)...")
        time.sleep(args.interval * 60)

if __name__ == "__main__":
    main()
