#!/usr/bin/env python3
"""
Experiment: Modern Scraper Comparison
======================================
Compares the current requests-based scraping approach against Crawl4AI
(Playwright-backed async browser) across all configured portal domains.

Usage:
    python3 scratch/experiment_modern_scraper.py
    python3 scratch/experiment_modern_scraper.py --portals "https://www.epicsports.in/" "https://football.scoopnonstop.com/"
    python3 scratch/experiment_modern_scraper.py --match-url "https://www.epicsports.in/2026/06/sweden-vs-tunisia.html"
"""
import asyncio
import json
import os
import re
import sys
import time
import argparse
from datetime import datetime
from urllib.parse import urlparse, urljoin
from html.parser import HTMLParser

# Add project root to path so we can import project modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from automation_config import load_automation_config, get_scheduler_config

# ---------------------------------------------------------------------------
# Current Approach: requests + EpicLinkParser (copied from generate_player.py)
# ---------------------------------------------------------------------------
class EpicLinkParser(HTMLParser):
    """Mirrors the parser from generate_player.py for fair comparison."""
    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.results = []
        self.current_tag = None
        self.current_attrs = {}
        self.current_text = []
        self.open_tags = []

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
        is_in_ignored = any(is_ignored for _, is_ignored in self.open_tags)
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
                match = re.search(r"'(https?://[^'\s]+)'|\"(https?://[^\"\s]+)\"", onclick_val)
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


def scrape_with_requests(url, timeout=15):
    """Current approach: requests.get + EpicLinkParser."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    start = time.time()
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        return {
            "method": "requests",
            "url": url,
            "success": False,
            "error": str(e),
            "time_ms": int((time.time() - start) * 1000),
            "links": [],
            "iframes": [],
            "stream_keywords_found": [],
            "html_length": 0,
        }

    parser = EpicLinkParser(url)
    parser.feed(html)

    links = [r for r in parser.results if r["tag"] in ("a", "button")]
    iframes = [r for r in parser.results if r["tag"] == "iframe"]

    # Detect stream-related patterns in the HTML
    stream_keywords = []
    for pattern in [r"\.m3u8", r"\.mpd", r"jwplayer", r"hls\.js", r"shaka", r"videojs", r"clappr"]:
        if re.search(pattern, html, re.IGNORECASE):
            stream_keywords.append(pattern.replace("\\", ""))

    elapsed = int((time.time() - start) * 1000)
    return {
        "method": "requests",
        "url": url,
        "success": True,
        "error": None,
        "time_ms": elapsed,
        "links": [{"text": l["text"][:80], "url": l["url"], "tag": l["tag"]} for l in links],
        "iframes": [{"text": i["text"][:80], "url": i["url"]} for i in iframes],
        "stream_keywords_found": stream_keywords,
        "html_length": len(html),
        "link_count": len(links),
        "iframe_count": len(iframes),
    }


# ---------------------------------------------------------------------------
# Modern Approach: Crawl4AI with Playwright
# ---------------------------------------------------------------------------
async def scrape_with_crawl4ai(url, timeout_ms=25000):
    """Modern approach: Crawl4AI AsyncWebCrawler with full JS execution."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

    browser_config = BrowserConfig(
        headless=True,
        text_mode=False,
        extra_args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"],
    )

    run_config = CrawlerRunConfig(
        wait_until="networkidle",
        page_timeout=timeout_ms,
        # Execute JS to scroll and trigger lazy-loaded content
        js_code=[
            "window.scrollTo(0, document.body.scrollHeight);",
            "await new Promise(r => setTimeout(r, 1500));",
            "window.scrollTo(0, 0);",
        ],
        # Block unnecessary resource types to speed up crawling
        excluded_tags=["style", "script[src*='ads']", "script[src*='analytics']"],
    )

    start = time.time()
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)

            if not result.success:
                return {
                    "method": "crawl4ai",
                    "url": url,
                    "success": False,
                    "error": result.error_message or "Unknown error",
                    "time_ms": int((time.time() - start) * 1000),
                    "links": [],
                    "iframes": [],
                    "stream_keywords_found": [],
                    "html_length": 0,
                }

            html = result.html or ""

            # Parse the fully rendered HTML with EpicLinkParser for fair comparison
            parser = EpicLinkParser(url)
            parser.feed(html)

            links = [r for r in parser.results if r["tag"] in ("a", "button")]
            iframes = [r for r in parser.results if r["tag"] == "iframe"]

            # Detect stream-related patterns
            stream_keywords = []
            for pattern in [r"\.m3u8", r"\.mpd", r"jwplayer", r"hls\.js", r"shaka", r"videojs", r"clappr"]:
                if re.search(pattern, html, re.IGNORECASE):
                    stream_keywords.append(pattern.replace("\\", ""))

            elapsed = int((time.time() - start) * 1000)

            # Extract discovered links from Crawl4AI's own link extraction
            crawl4ai_links = []
            if hasattr(result, "links") and result.links:
                for link_info in result.links.get("internal", []) + result.links.get("external", []):
                    crawl4ai_links.append({
                        "text": (link_info.get("text") or "")[:80],
                        "url": link_info.get("href", ""),
                    })

            return {
                "method": "crawl4ai",
                "url": url,
                "success": True,
                "error": None,
                "time_ms": elapsed,
                "links": [{"text": l["text"][:80], "url": l["url"], "tag": l["tag"]} for l in links],
                "iframes": [{"text": i["text"][:80], "url": i["url"]} for i in iframes],
                "crawl4ai_native_links": crawl4ai_links[:50],  # Cap at 50
                "stream_keywords_found": stream_keywords,
                "html_length": len(html),
                "link_count": len(links),
                "iframe_count": len(iframes),
                "markdown_length": len(result.markdown or ""),
            }

    except Exception as e:
        return {
            "method": "crawl4ai",
            "url": url,
            "success": False,
            "error": str(e),
            "time_ms": int((time.time() - start) * 1000),
            "links": [],
            "iframes": [],
            "stream_keywords_found": [],
            "html_length": 0,
        }


# ---------------------------------------------------------------------------
# Comparison Runner
# ---------------------------------------------------------------------------
async def compare_portal(url):
    """Run both scrapers on the same URL and return comparison."""
    domain = urlparse(url).netloc
    print(f"\n{'='*60}")
    print(f"  Portal: {domain}")
    print(f"  URL:    {url}")
    print(f"{'='*60}")

    # Pass A: Current approach
    print(f"  [A] requests + HTMLParser ...", end=" ", flush=True)
    result_requests = scrape_with_requests(url)
    status_a = "✅" if result_requests["success"] else "❌"
    print(f"{status_a}  {result_requests.get('link_count', 0)} links, {result_requests.get('iframe_count', 0)} iframes, {result_requests['time_ms']}ms")

    # Pass B: Modern approach
    print(f"  [B] Crawl4AI + Playwright ...", end=" ", flush=True)
    result_crawl4ai = await scrape_with_crawl4ai(url)
    status_b = "✅" if result_crawl4ai["success"] else "❌"
    print(f"{status_b}  {result_crawl4ai.get('link_count', 0)} links, {result_crawl4ai.get('iframe_count', 0)} iframes, {result_crawl4ai['time_ms']}ms")

    # Compute delta
    links_delta = result_crawl4ai.get("link_count", 0) - result_requests.get("link_count", 0)
    iframes_delta = result_crawl4ai.get("iframe_count", 0) - result_requests.get("iframe_count", 0)
    html_delta = result_crawl4ai.get("html_length", 0) - result_requests.get("html_length", 0)

    js_rendered_content = html_delta > 500  # If Crawl4AI got >500 chars more, JS likely added content

    comparison = {
        "domain": domain,
        "url": url,
        "requests": result_requests,
        "crawl4ai": result_crawl4ai,
        "delta": {
            "links": links_delta,
            "iframes": iframes_delta,
            "html_chars": html_delta,
            "js_rendered_content_detected": js_rendered_content,
        },
        "winner": "crawl4ai" if (links_delta > 0 or iframes_delta > 0) else ("tie" if links_delta == 0 and iframes_delta == 0 else "requests"),
    }

    indicator = "🏆" if comparison["winner"] == "crawl4ai" else ("🤝" if comparison["winner"] == "tie" else "📉")
    print(f"  Result: {indicator} {comparison['winner'].upper()} | Δlinks={links_delta:+d} Δiframes={iframes_delta:+d} JS-content={'YES' if js_rendered_content else 'no'}")

    return comparison


async def run_experiment(portal_urls):
    """Run the full experiment across all portals."""
    print("\n" + "=" * 70)
    print("  MODERN SCRAPER EXPERIMENT — requests vs Crawl4AI")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Portals: {len(portal_urls)}")
    print("=" * 70)

    results = []
    for url in portal_urls:
        try:
            comparison = await compare_portal(url)
            results.append(comparison)
        except Exception as e:
            print(f"  ⚠ ERROR on {url}: {e}")
            results.append({
                "domain": urlparse(url).netloc,
                "url": url,
                "error": str(e),
                "winner": "error",
            })

    # Summary table
    print("\n\n" + "=" * 100)
    print("  SUMMARY")
    print("=" * 100)
    print(f"  {'Domain':<35} {'Req Links':>10} {'C4AI Links':>10} {'Δ Links':>8} {'Req IF':>7} {'C4AI IF':>7} {'Δ IF':>6} {'JS?':>4} {'Winner':>10}")
    print("  " + "-" * 96)

    crawl4ai_wins = 0
    ties = 0
    requests_wins = 0
    errors = 0

    for r in results:
        if r.get("winner") == "error":
            print(f"  {r['domain']:<35} {'ERROR':>10}")
            errors += 1
            continue
        req = r.get("requests", {})
        c4a = r.get("crawl4ai", {})
        delta = r.get("delta", {})
        js = "YES" if delta.get("js_rendered_content_detected") else ""
        winner = r.get("winner", "?")
        print(f"  {r['domain']:<35} {req.get('link_count', '?'):>10} {c4a.get('link_count', '?'):>10} {delta.get('links', 0):>+8} {req.get('iframe_count', '?'):>7} {c4a.get('iframe_count', '?'):>7} {delta.get('iframes', 0):>+6} {js:>4} {winner:>10}")

        if winner == "crawl4ai":
            crawl4ai_wins += 1
        elif winner == "tie":
            ties += 1
        else:
            requests_wins += 1

    print("  " + "-" * 96)
    print(f"  Totals: Crawl4AI wins={crawl4ai_wins}  Ties={ties}  Requests wins={requests_wins}  Errors={errors}")
    print()

    # Save results
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraper_comparison_results.json")
    report = {
        "experiment": "modern_scraper_comparison",
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "portal_count": len(portal_urls),
        "summary": {
            "crawl4ai_wins": crawl4ai_wins,
            "ties": ties,
            "requests_wins": requests_wins,
            "errors": errors,
        },
        "results": results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"  📊 Full results saved to: {output_path}")

    return report


def main():
    parser = argparse.ArgumentParser(description="Compare requests vs Crawl4AI scraping on portal domains.")
    parser.add_argument(
        "--portals", nargs="*",
        help="Specific portal URLs to test (overrides config)."
    )
    parser.add_argument(
        "--match-url", type=str,
        help="A specific match page URL to test deep crawling on."
    )
    parser.add_argument(
        "--max-portals", type=int, default=0,
        help="Limit number of portals to test (0 = all)."
    )
    args = parser.parse_args()

    if args.portals:
        portal_urls = args.portals
    else:
        config = load_automation_config()
        scheduler_config = get_scheduler_config(config)
        portal_urls = scheduler_config.get("discovery_portals") or scheduler_config.get("auto_discover_portals") or []

    if args.max_portals > 0:
        portal_urls = portal_urls[:args.max_portals]

    if not portal_urls:
        print("No portal URLs found. Provide --portals or ensure master_config.json has discovery_portals.")
        sys.exit(1)

    asyncio.run(run_experiment(portal_urls))


if __name__ == "__main__":
    main()
