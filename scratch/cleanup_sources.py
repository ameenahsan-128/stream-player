#!/usr/bin/env python3
import json
import os
from urllib.parse import urlparse

SCHEDULE_PATH = "data/match_schedule.json"

IGNORED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
    ".css", ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".js", ".json"
}

def clean_url(url):
    if not url:
        return False
    try:
        parsed = urlparse(url)
        path = parsed.path.lower()
        if any(path.endswith(ext) for ext in IGNORED_EXTENSIONS):
            return False
        return True
    except Exception:
        return False

def main():
    if not os.path.exists(SCHEDULE_PATH):
        print(f"[-] Schedule path {SCHEDULE_PATH} does not exist.")
        return

    with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
        schedule = json.load(f)

    cleaned_count = 0
    cleaned_records_count = 0

    for match in schedule:
        # 1. Clean source_url list/string
        source_val = match.get("source_url")
        if isinstance(source_val, list):
            new_sources = [url for url in source_val if clean_url(url)]
            if len(new_sources) != len(source_val):
                cleaned_count += len(source_val) - len(new_sources)
                match["source_url"] = new_sources if len(new_sources) > 1 else (new_sources[0] if new_sources else "")
        elif isinstance(source_val, str) and source_val:
            if not clean_url(source_val):
                cleaned_count += 1
                match["source_url"] = ""

        # 2. Clean source_records
        records = match.get("source_records")
        if isinstance(records, list):
            new_records = []
            for rec in records:
                url = rec.get("url") or rec.get("source_url")
                if clean_url(url):
                    new_records.append(rec)
                else:
                    cleaned_records_count += 1
            match["source_records"] = new_records

    if cleaned_count > 0 or cleaned_records_count > 0:
        with open(SCHEDULE_PATH, "w", encoding="utf-8") as f:
            json.dump(schedule, f, indent=2)
        print(f"[+] Cleaned {cleaned_count} source URLs and {cleaned_records_count} source records from schedule.")
    else:
        print("[*] No junk sources found in match_schedule.json.")

if __name__ == "__main__":
    main()
