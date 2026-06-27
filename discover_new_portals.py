import os
import sys
import json
import re
import requests
from urllib.parse import urlparse
from datetime import datetime, timezone

def load_config():
    with open("master_config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config):
    with open("master_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

def load_schedule():
    with open("data/match_schedule.json", "r", encoding="utf-8") as f:
        return json.load(f)

def search_firecrawl(query, api_key, limit=20):
    try:
        payload = {
            "query": query,
            "limit": limit
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        res = requests.post("https://api.firecrawl.dev/v1/search", json=payload, headers=headers, timeout=25)
        res.raise_for_status()
        data = res.json()
        if data.get("success") and "data" in data:
            return [item.get("url") for item in data["data"] if item.get("url")]
        return []
    except Exception as e:
        print(f"[-] Firecrawl search failed for '{query}': {e}", file=sys.stderr)
        return []

def main():
    config = load_config()
    scheduler_config = config.setdefault("scheduler", {})
    firecrawl_key = os.environ.get("FIRECRAWL_API_KEY") or scheduler_config.get("firecrawl_api_key")
    if not firecrawl_key:
        print("[-] Error: FIRECRAWL_API_KEY not configured in env or master_config.json", file=sys.stderr)
        sys.exit(1)
        
    schedule = load_schedule()
    active_matches = []
    
    # Get active/pending/recent matches
    for m in schedule:
        status = m.get("status", "pending")
        if status in ("pending", "processing", "live"):
            active_matches.append(m)
            
    if not active_matches:
        # Fall back to checking any match in schedule
        print("[*] No active matches; checking top fixtures in schedule.")
        active_matches = schedule[:5]
        
    discovered_origins = set()
    
    # Automatically add manual target provided by user as first priority discovery source
    user_origins = [
        "https://stplyrv23.blogspot.com/"
    ]
    for u in user_origins:
        discovered_origins.add(u)
    
    for match in active_matches:
        t1 = match.get("team1")
        t2 = match.get("team2")
        if not t1 or not t2:
            continue
            
        print(f"[*] Discovering portals for: {t1} vs {t2}")
        queries = [
            f'site:blogspot.com "{t1} vs {t2}"',
            f'site:blogspot.com "{t1} {t2}"'
        ]
        
        for q in queries:
            urls = search_firecrawl(q, firecrawl_key, limit=20)
            for u in urls:
                parsed = urlparse(u)
                if "blogspot.com" in parsed.netloc:
                    origin = f"https://{parsed.netloc}/"
                    discovered_origins.add(origin)
                    
    # Also add some default search strings to find active templates
    extra_queries = [
        'site:blogspot.com "enjoy-live-match"',
        'site:blogspot.com "base-button-structure"'
    ]
    for q in extra_queries:
        urls = search_firecrawl(q, firecrawl_key, limit=15)
        for u in urls:
            parsed = urlparse(u)
            if "blogspot.com" in parsed.netloc:
                origin = f"https://{parsed.netloc}/"
                discovered_origins.add(origin)
                
    # Filter out our own player blog
    player_blog_url = config.get("player_blog", {}).get("player_slots", [{}])[0].get("url", "")
    player_blog_domain = urlparse(player_blog_url).netloc.lower() if player_blog_url else ""
    
    new_portals = []
    
    current_portals = set(config.get("player_blog", {}).get("auto_discover_portals", []))
    current_portals.update(scheduler_config.get("auto_discover_portals", []))
    current_portals.update(scheduler_config.get("discovery_portals", []))
    
    for orig in sorted(list(discovered_origins)):
        domain = urlparse(orig).netloc.lower()
        if player_blog_domain and player_blog_domain in domain:
            continue
        if "google" in domain or "blogger" in domain:
            continue
            
        is_new = True
        for curr in current_portals:
            if domain in curr.lower():
                is_new = False
                break
                
        if is_new:
            # Quick probe check
            try:
                r = requests.get(orig, timeout=5)
                if r.status_code == 200:
                    new_portals.append(orig)
                    print(f"[+] Discovered new valid portal: {orig}")
            except Exception:
                pass
                
    if new_portals:
        # Update config lists
        player_blog_portals = config.setdefault("player_blog", {}).setdefault("auto_discover_portals", [])
        sched_auto_portals = scheduler_config.setdefault("auto_discover_portals", [])
        sched_disc_portals = scheduler_config.setdefault("discovery_portals", [])
        trusted_domains = scheduler_config.setdefault("trusted_source_domains", [])
        
        for port in new_portals:
            domain = urlparse(port).netloc
            if port not in player_blog_portals:
                player_blog_portals.append(port)
            if port not in sched_auto_portals:
                sched_auto_portals.append(port)
            if port not in sched_disc_portals:
                sched_disc_portals.append(port)
            if domain not in trusted_domains:
                trusted_domains.append(domain)
            # Check www version too
            if domain.startswith("www."):
                non_www = domain[4:]
                if non_www not in trusted_domains:
                    trusted_domains.append(non_www)
            else:
                www_ver = f"www.{domain}"
                if www_ver not in trusted_domains:
                    trusted_domains.append(www_ver)
                    
        save_config(config)
        print(f"[+] Successfully added {len(new_portals)} new portals to master_config.json")
    else:
        print("[*] No new portals discovered.")

if __name__ == "__main__":
    main()
