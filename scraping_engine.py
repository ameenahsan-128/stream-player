import os
import sys
import time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

class SelfHostedScraper:
    def __init__(self, headless=True):
        self.headless = headless

    def scrape_url(self, url, block_ads=True, whitelist_domains=None, timeout_ms=15000):
        """
        Renders a page using Chromium and extracts its rendered HTML.
        Implements smart ad-blocking, keeping whitelist domains alive.
        """
        if whitelist_domains is None:
            whitelist_domains = [
                "masszipp3.github.io", 
                "netmirror.info", 
                "epicsports", 
                "sportsflair", 
                "sportscorner3697", 
                "rexdexsports",
                "stplyrv23",
                "blogspot"
            ]

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage"
                    ]
                )
                
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720},
                    device_scale_factor=1,
                    bypass_csp=True
                )
                
                page = context.new_page()
                page.set_default_timeout(timeout_ms)

                # Request Interceptor for Smart Ad-Blocking
                if block_ads:
                    def intercept_route(route):
                        req_url = route.request.url.lower()
                        ad_keywords = [
                            "google-analytics", "doubleclick", "adservice", "popads", 
                            "propellerads", "exoclick", "onclick", "popunder", 
                            "adsterra", "mgid", "taboola", "outbrain", "yandex", 
                            "histats", "adnxs", "amazon-adsystem"
                        ]
                        
                        is_ad = any(kw in req_url for kw in ad_keywords)
                        is_whitelisted = any(domain in req_url for domain in whitelist_domains)
                        
                        # Only abort if it's an ad and not whitelisted
                        if is_ad and not is_whitelisted:
                            route.abort()
                        else:
                            route.continue_()
                    
                    page.route("**/*", intercept_route)

                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                
                # Perform a gentle scroll to trigger lazy-loaded frame scripts
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2);")
                    time.sleep(1.0)
                    page.evaluate("window.scrollTo(0, 0);")
                except Exception:
                    pass
                
                # Wait for any active iframes or dynamic scripts to settle
                time.sleep(2.0)

                html_content = page.content()
                
                # Normalize custom attributes (e.g. converting divs with iframe tags back to actual tags if any script alters them)
                # Ensure it contains the needed text
                return html_content, True
            except Exception as e:
                print(f"[-] Self-hosted scrape failed for {url}: {e}", file=sys.stderr)
                return None, False
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

# Singleton instance to reuse across operations
_scraper = None

def get_scraper(headless=True):
    global _scraper
    if _scraper is None:
        _scraper = SelfHostedScraper(headless=headless)
    return _scraper

def fetch_page_with_playwright(url, headless=True):
    scraper = get_scraper(headless=headless)
    return scraper.scrape_url(url)
