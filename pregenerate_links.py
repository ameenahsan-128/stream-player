#!/usr/bin/env python3
"""Pre-generate stream link button HTML for upcoming matches.

These link buttons point through the player blog's master URL or the dedicated
Blogger player posts. If dedicated posts do not exist yet, they are pre-created
on Blogger and their URLs are saved to the match schedule.
"""
import json
import os
import re
import time
import requests

MASTER_PLAYER_URL = "https://qtwc2022.blogspot.com/p/world-cup-1.html"
NUM_LINKS = 4  # Pre-generate 4 placeholder links per match
LINKS_DIR = "data/links"


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def generate_links_html(match_name, player_url, num_links=NUM_LINKS):
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
        url = f"{player_url}?link={link_num}&cb={cb}"
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


def create_blogger_post(config, access_token, title, html_content):
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
    response = requests.post(url, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    res_data = response.json()
    return res_data.get("id"), res_data.get("url")


def get_player_template():
    player_dir = "data/players"
    if os.path.exists(player_dir):
        files = sorted(
            [f for f in os.listdir(player_dir) if f.startswith("player_") and f.endswith(".html")],
            key=lambda f: os.path.getmtime(os.path.join(player_dir, f)),
            reverse=True,
        )
        for cand in files:
            cand_path = os.path.join(player_dir, cand)
            try:
                with open(cand_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if "STREAM_LINKS" in content or "Shaka" in content or "shaka" in content:
                    return content
            except Exception:
                pass
    # Basic fallback template
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Live Stream Player</title>
</head>
<body style="background:#000;color:#fff;font-family:sans-serif;text-align:center;padding:50px;">
<h1>Live Stream Player</h1>
<p>Stream is loading...</p>
</body>
</html>"""


def main():
    # Clear all existing files in links directory as requested by user
    if os.path.exists(LINKS_DIR):
        for filename in os.listdir(LINKS_DIR):
            file_path = os.path.join(LINKS_DIR, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    import shutil
                    shutil.rmtree(file_path)
            except Exception as e:
                print(f'Failed to delete {file_path}. Reason: {e}')

    with open("data/match_schedule.json") as f:
        schedule = json.load(f)

    with open("master_config.json") as f:
        config = json.load(f)

    player_blog_config = config.get("player_blog", {})

    access_token = None
    if player_blog_config.get("client_id") and player_blog_config.get("client_secret") and player_blog_config.get("refresh_token"):
        try:
            print("[*] Refreshing player blog access token...")
            access_token = get_access_token(player_blog_config)
            print("[+] Access token retrieved successfully.")
        except Exception as e:
            print(f"[-] Failed to get Blogger access token: {e}")
            print("[*] Will fall back to default URLs.")

    template_html = get_player_template()
    print(f"[*] Loaded player template ({len(template_html)} chars)")

    os.makedirs(LINKS_DIR, exist_ok=True)
    generated = 0
    schedule_changed = False

    for match in schedule:
        status = match.get("status", "pending")
        if status in ("completed", "ended"):
            continue

        match_name = match["match_name"]

        # Check if match has a blogger post id, if not and we have access token, pre-create it!
        post_id = str(match.get("blogger_post_id") or "").strip()
        post_url = str(match.get("blogger_post_url") or "").strip()

        if (not post_id or post_id.startswith("YOUR_")) and access_token:
            print(f"[*] Pre-creating Blogger player post for match: {match_name}")
            try:
                title = f"{match_name} Live Stream"
                post_id, post_url = create_blogger_post(player_blog_config, access_token, title, template_html)
                match["blogger_post_id"] = post_id
                match["blogger_post_url"] = post_url
                match["player_slot_url"] = post_url
                schedule_changed = True
                print(f"[+] Created Blogger post for {match_name}: {post_url}")
            except Exception as e:
                print(f"[-] Failed to pre-create Blogger post for {match_name}: {e}")

        slug = slugify(match_name)
        filepath = os.path.join(LINKS_DIR, f"links_{slug}.html")

        # Determine the player URL for this match
        player_url = (
            match.get("blogger_post_url")
            or match.get("player_slot_url")
            or match.get("new_blogger_page_url")
            or match.get("new_blogger_post_url")
            or MASTER_PLAYER_URL
        )

        html = generate_links_html(match_name, player_url)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[+] Generated {filepath} ({match_name}) with player_url={player_url}")
        generated += 1

    if schedule_changed:
        with open("data/match_schedule.json", "w", encoding="utf-8") as f:
            json.dump(schedule, f, indent=2)
        print("[+] Saved updated match schedule with new Blogger post URLs.")

    print(f"\n[+] Done. Generated {generated} link files in {LINKS_DIR}/")


if __name__ == "__main__":
    main()
