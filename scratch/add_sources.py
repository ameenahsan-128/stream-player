import json
from datetime import datetime, timezone
from urllib.parse import urlparse

SCHEDULE_PATH = "data/match_schedule.json"

new_urls = [
    "https://www.goatsports.xyz/p/best-fifa-world-cup-fans-in-history.html",
    "https://e.netmirror.info/p/best-goals-in-fifa-world-cup-history.html"
]

def main():
    with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
        schedule = json.load(f)

    for match in schedule:
        if "Spain" in match.get("match_name", ""):
            # Append to source_url
            sources = match.get("source_url", [])
            if isinstance(sources, str):
                sources = [sources] if sources else []
            for url in new_urls:
                if url not in sources:
                    sources.append(url)
            match["source_url"] = sources

            # Append to source_records
            records = match.get("source_records", [])
            existing_urls = {rec.get("url") or rec.get("source_url") for rec in records}
            for url in new_urls:
                if url not in existing_urls:
                    domain = urlparse(url).netloc.lower()
                    records.append({
                        "url": url,
                        "domain": domain,
                        "score": 100,
                        "discovered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "fail_count": 0
                    })
            match["source_records"] = records
            # Force scheduler update immediately
            match["last_run_time"] = "2026-06-15T00:00:00Z"
            print(f"[+] Added new source links to {match['match_name']}.")

    with open(SCHEDULE_PATH, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2)

if __name__ == "__main__":
    main()
