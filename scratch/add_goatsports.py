import json
import os

schedule_path = "data/match_schedule.json"

if os.path.exists(schedule_path):
    with open(schedule_path, "r", encoding="utf-8") as f:
        schedule = json.load(f)
        
    target_url = "https://e.netmirror.info/p/best-goals-in-fifa-world-cup-history.html"
    updated_count = 0
    
    for match in schedule:
        source_urls = match.setdefault("source_url", [])
        if target_url not in source_urls:
            source_urls.append(target_url)
            updated_count += 1
            
    with open(schedule_path, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2)
        
    print(f"[+] Successfully added netmirror target URL to {updated_count} match schedule entries.")
else:
    print("[-] Error: match_schedule.json not found.")
