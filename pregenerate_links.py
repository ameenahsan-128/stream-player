#!/usr/bin/env python3
"""Pre-generate stream link button HTML for upcoming matches.

These link buttons point through the player blog's master URL with ?link=N
parameters. When the scheduler populates real streams before kickoff,
these links automatically work.
"""
import json
import os
import re
import time

MASTER_PLAYER_URL = "https://qtwc2022.blogspot.com/p/world-cup-1.html"
NUM_LINKS = 4  # Pre-generate 4 placeholder links per match
LINKS_DIR = "data/links"


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def generate_links_html(match_name, num_links=NUM_LINKS):
    cb = int(time.time())
    qualities = [
        ("720p HD", "HLS", "ENG", True),
        ("720p HD", "HLS", "ENG", True),
        ("Auto Quality", "HLS", "ENG", True),
        ("Auto Quality", "HLS", "ENG", True),
    ]

    buttons = []
    for i in range(num_links):
        link_num = i + 1
        url = f"{MASTER_PLAYER_URL}?link={link_num}&cb={cb}"
        q, proto, lang, iphone = qualities[i % len(qualities)]
        subtitle = f"{q} · {proto} · {lang}"
        if iphone:
            subtitle += " · 🍎 Works on iPhone"

        buttons.append(f'''  <a class="stream-btn" href="{url}" target="_blank">
    <span class="stream-title">{match_name} — Link {link_num}</span>
    <span class="stream-subtitle">{subtitle}</span>
  </a>''')

    return f'''<div style="font-family:'Segoe UI',Roboto,Helvetica,sans-serif; max-width:650px; margin: 20px auto; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 0 10px;">
  <style>
    .stream-btn {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      width: 100%;
      max-width: 550px;
      margin-bottom: 15px;
      padding: 16px 24px;
      background: linear-gradient(135deg, #e63946 0%, #b81d24 100%);
      color: #ffffff;
      text-decoration: none;
      border-radius: 12px;
      border: 1px solid #ff4d5a;
      box-shadow: 0 4px 15px rgba(230, 57, 70, 0.3);
      box-sizing: border-box;
      transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
      cursor: pointer;
    }}
    .stream-btn:hover {{
      background: linear-gradient(135deg, #ff4d5a 0%, #e63946 100%);
      border-color: #ff808b;
      transform: translateY(-2px);
      box-shadow: 0 8px 25px rgba(230, 57, 70, 0.5);
    }}
    .stream-btn:active {{
      transform: translateY(1px);
      box-shadow: 0 2px 10px rgba(230, 57, 70, 0.3);
    }}
    .stream-title {{
      font-size: 16px;
      font-weight: 700;
      letter-spacing: 0.5px;
      margin-bottom: 6px;
      text-transform: uppercase;
      color: #ffffff;
      text-shadow: 0 1px 2px rgba(0,0,0,0.2);
      text-align: center;
    }}
    .stream-subtitle {{
      font-size: 13px;
      font-weight: 600;
      color: #00ff88;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      text-align: center;
    }}
  </style>
{chr(10).join(buttons)}
</div>'''


def main():
    with open("data/match_schedule.json") as f:
        schedule = json.load(f)

    os.makedirs(LINKS_DIR, exist_ok=True)
    generated = 0

    for match in schedule:
        status = match.get("status", "pending")
        if status in ("completed", "ended"):
            continue

        match_name = match["match_name"]
        slug = slugify(match_name)
        filepath = os.path.join(LINKS_DIR, f"links_{slug}.html")

        # Don't overwrite if already exists with real scraped links
        if os.path.exists(filepath):
            with open(filepath) as f:
                existing = f.read()
            # If it has more than NUM_LINKS links, it was scraped — skip
            link_count = existing.count("stream-btn")
            if link_count > NUM_LINKS:
                print(f"[*] Skipping {match_name} — already has {link_count} scraped links")
                continue

        html = generate_links_html(match_name)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[+] Generated {filepath} ({match_name})")
        generated += 1

    print(f"\n[+] Done. Generated {generated} link files in {LINKS_DIR}/")


if __name__ == "__main__":
    main()
