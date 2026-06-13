#!/usr/bin/env python3
import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta, timezone
import re

from automation_config import get_portal_blog_config, get_scheduler_config, has_oauth, load_automation_config
from pipeline_storage import archive_completed_matches, ensure_runtime_dirs, load_schedule, match_key, save_schedule
from portal_renderer import (
    IST,
    get_channel_info,
    parse_match_time,
    preview_post_title,
    render_preview_post,
    render_streaming_page,
    slugify_match_name,
    split_teams,
    streaming_page_title,
)

SCHEDULE_FILE = "match_schedule.json"
CONFIG_FILE = "new_blogger_config.json"

# Exact ad code scripts and popup wrappers matching goforsports.net
DEFAULT_AD_TOP = """
<div align="center" style="margin: 15px 0;">
  <script type="text/javascript">
    atOptions = {
      'key' : '26752c18ca8361bba098d31342583042',
      'format' : 'iframe',
      'height' : 250,
      'width' : 300,
      'params' : {}
    };
  </script>
  <script type="text/javascript" src="https://www.highperformanceformat.com/26752c18ca8361bba098d31342583042/invoke.js"></script>
</div>
"""

DEFAULT_AD_POPUP = """
<!--Popup Ad Overlay-->
<div id="popup-ad-overlay" style="align-items: center; background: rgba(0, 0, 0, 0.6); display: none; height: 100%; justify-content: center; left: 0px; position: fixed; top: 0px; width: 100%; z-index: 99999;">
  <div style="background: rgb(255, 255, 255); border-radius: 8px; padding: 10px; position: relative;">
    <button onclick="document.getElementById('popup-ad-overlay').style.display='none'" style="background: rgb(51, 51, 51); border: none; color: white; cursor: pointer; font-size: 16px; height: 26px; line-height: 1; position: absolute; right: -12px; top: -12px; width: 26px; border-radius: 50%;">&times;</button>
    <script type="text/javascript">
      atOptions = {
        'key' : '26752c18ca8361bba098d31342583042',
        'format' : 'iframe',
        'height' : 250,
        'width' : 300,
        'params' : {}
      };
    </script>
    <script type="text/javascript" src="https://www.highperformanceformat.com/26752c18ca8361bba098d31342583042/invoke.js"></script>
  </div>
</div>
<script type="text/javascript">
  window.addEventListener('load', function() {
    setTimeout(function() {
      const overlay = document.getElementById('popup-ad-overlay');
      if (overlay) overlay.style.display = 'flex';
    }, 3000); // Trigger popup after 3 seconds
  });
</script>
<!-- PropellerAds / Monetag Integration Scripts -->
<script type="text/javascript" src="https://throughalivemedication.com/45/96/a9/4596a9a27ac7c137dd494fd1f200edbb.js"></script>
<script type="text/javascript" src="https://pl17973087.effectivecpmnetwork.com/45/96/a9/4596a9a27ac7c137dd494fd1f200edbb.js"></script>
<script type="text/javascript" src="https://pl17973277.effectivecpmnetwork.com/66/f1/17/66f11775fe2744312299821ac71b38f1.js"></script>
"""

def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

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

def update_blogger_post(config, access_token, post_id, title, html_content):
    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/{post_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "kind": "blogger#post",
        "id": post_id,
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    response = requests.patch(url, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    return response.json().get("url")

def create_blogger_page(config, access_token, title, html_content):
    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "kind": "blogger#page",
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    response = requests.post(url, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    res_data = response.json()
    return res_data.get("id"), res_data.get("url")

def update_blogger_page(config, access_token, page_id, title, html_content):
    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "kind": "blogger#page",
        "id": page_id,
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    response = requests.patch(url, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    return response.json().get("url")


def normalize_title(title):
    return re.sub(r"\s+", " ", (title or "").strip()).lower()


MATCH_ALIASES = {
    "qat": "qatar",
    "qater": "qatar",
    "switz": "switzerland",
    "swi": "switzerland",
    "scot": "scotland",
    "sco": "scotland",
    "turk": "turkiye",
    "turkey": "turkiye",
    "aus": "australia",
    "bra": "brazil",
    "mor": "morocco",
    "para": "paraguay",
    "par": "paraguay"
}

MATCH_STOP_WORDS = {
    "fifa", "world", "cup", "2026", "match", "preview", "live", "info",
    "stream", "streaming", "portal", "watch", "and", "the", "vs", "v"
}


def match_words(text):
    words = []
    for raw in re.findall(r"[a-z0-9]+", (text or "").lower()):
        word = MATCH_ALIASES.get(raw, raw)
        if word not in MATCH_STOP_WORDS and (len(word) > 2 or word == "usa"):
            words.append(word)
    return set(words)


def item_matches_title_or_match(item, title, match_name=None):
    item_title = item.get("title", "")
    if normalize_title(item_title) == normalize_title(title):
        return True
    if not match_name:
        return False
    expected = match_words(match_name)
    actual = match_words(item_title)
    return len(expected) >= 2 and expected.issubset(actual)


def find_existing_blogger_post(config, access_token, title, match_name=None):
    blog_id = config.get("blog_id")
    headers = {"Authorization": f"Bearer {access_token}"}

    search_url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/search"
    try:
        response = requests.get(
            search_url,
            headers=headers,
            params={"q": title, "fetchBodies": "false"},
            timeout=20
        )
        response.raise_for_status()
        for item in response.json().get("items", []):
            if item_matches_title_or_match(item, title, match_name):
                return item.get("id"), item.get("url")
    except Exception as e:
        print(f"[!] Post search failed, falling back to recent post list: {e}")

    list_url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts"
    page_token = None
    for _ in range(5):
        params = {"fetchBodies": "false", "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(list_url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        for item in data.get("items", []):
            if item_matches_title_or_match(item, title, match_name):
                return item.get("id"), item.get("url")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return None, None


def find_existing_blogger_page(config, access_token, title, match_name=None):
    blog_id = config.get("blog_id")
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages"
    page_token = None

    for _ in range(5):
        params = {"fetchBodies": "false", "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        for item in data.get("items", []):
            if item_matches_title_or_match(item, title, match_name):
                return item.get("id"), item.get("url")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return None, None

def generate_thumbnail(team1, team2, match_time_str, output_path, league="", channel=""):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[!] Pillow library not found. Skipping image generation.")
        return False

    template_path = "match_thumbnail.png"
    if os.path.exists(template_path):
        img = Image.open(template_path).convert("RGB")
    else:
        # Fallback gradient
        img = Image.new("RGB", (800, 800), color="#0c0d14")
        draw = ImageDraw.Draw(img)
        for i in range(800):
            r = int(12 + (i / 800) * 10)
            g = int(13 + (i / 800) * 15)
            b = int(20 + (i / 800) * 25)
            draw.line([(0, i), (800, i)], fill=(r, g, b))

    draw = ImageDraw.Draw(img)
    width, height = img.size

    # Try standard system fonts
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "arial.ttf"
    ]
    font_title = None
    font_sub = None
    for p in font_paths:
        try:
            font_title = ImageFont.truetype(p, 42)
            font_sub = ImageFont.truetype(p, 22)
            break
        except Exception:
            continue

    if not font_title:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    # Semi-transparent card overlay
    overlay_y1 = int(height * 0.60)
    overlay_y2 = int(height * 0.90)

    # Draw card backing with subtle outline
    draw.rectangle([40, overlay_y1, width - 40, overlay_y2], fill=(12, 13, 20, 220), outline=(255, 255, 255, 30), width=1)

    # Format text
    team_text = f"{team1} VS {team2}"
    try:
        w_text = draw.textlength(team_text, font=font_title)
    except AttributeError:
        w_text = font_title.getsize(team_text)[0] if hasattr(font_title, 'getsize') else 350
    x_text = (width - w_text) // 2
    draw.text((x_text, overlay_y1 + 30), team_text, fill="#ffffff", font=font_title)

    # Format time
    try:
        if match_time_str.endswith("Z"):
            match_time_str = match_time_str[:-1] + "+00:00"
        match_dt = datetime.fromisoformat(match_time_str).astimezone(IST)
        time_text = match_dt.strftime("%d %b %Y | %I:%M %p IST").lstrip("0")
    except Exception:
        time_text = match_time_str

    try:
        w_time = draw.textlength(time_text, font=font_sub)
    except AttributeError:
        w_time = font_sub.getsize(time_text)[0] if hasattr(font_sub, 'getsize') else 250
    x_time = (width - w_time) // 2
    draw.text((x_time, overlay_y1 + 100), time_text, fill="#00e5ff", font=font_sub)

    detail_text = " | ".join(part for part in [league, channel] if part and part != "TBA")
    if detail_text:
        detail_text = detail_text[:70]
        try:
            w_detail = draw.textlength(detail_text, font=font_sub)
        except AttributeError:
            w_detail = font_sub.getsize(detail_text)[0] if hasattr(font_sub, 'getsize') else 250
        x_detail = (width - w_detail) // 2
        draw.text((x_detail, overlay_y1 + 140), detail_text, fill="#ffffff", font=font_sub)

    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "JPEG", quality=92)
    print(f"[+] Generated thumbnail image: {output_path}")
    return True

def generate_post_html(config, match, safe_name):
    match_name = match["match_name"]
    match_time_str = match["match_time"]
    
    name_parts = re.split(r'(?:Vs|vs|VS|\bv\b)', match_name)
    team1 = name_parts[0].strip() if len(name_parts) > 0 else "Team A"
    team2 = name_parts[1].strip() if len(name_parts) > 1 else "Team B"
    
    # Redirect button points to the Page URL
    page_url = match.get("new_blogger_page_url", "#")
    
    # Custom image URL
    image_base = config.get("image_base_url", "https://yourdomain.com/images/")
    thumbnail_url = f"{image_base}thumb_{safe_name}.jpg"
    
    # Ad layouts from configuration or default values
    ad_top = config.get("ad_code_top") or DEFAULT_AD_TOP
    ad_bottom = config.get("ad_code_bottom") or DEFAULT_AD_POPUP

    # Randomized WhatsApp and Telegram group redirect buttons
    whatsapp_groups = config.get("whatsapp_groups", [])
    telegram_channels = config.get("telegram_channels", [])
    
    wa_json = json.dumps(whatsapp_groups)
    tg_json = json.dumps(telegram_channels)
    
    social_buttons_html = ""
    social_script = ""
    
    if whatsapp_groups or telegram_channels:
        social_buttons_html += '<div style="text-align: center; margin: 15px 0; display: flex; justify-content: center; gap: 12px; flex-wrap: wrap;">'
        if whatsapp_groups:
            social_buttons_html += '<button onclick="openRandomWaGroup()" style="display: inline-block; background-color: #25d366; color: white; border: none; cursor: pointer; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">Join WhatsApp Group</button>'
        if telegram_channels:
            social_buttons_html += '<button onclick="openRandomTgGroup()" style="display: inline-block; background-color: #0088cc; color: white; border: none; cursor: pointer; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">Join Telegram Channel</button>'
        social_buttons_html += '</div>'
        
        social_script = f"""
<script type="text/javascript">
(function() {{
  const waPool = {wa_json};
  const tgPool = {tg_json};
  
  window.openRandomWaGroup = function() {{
    if (!waPool || waPool.length === 0) return;
    const idx = Math.floor(Math.random() * waPool.length);
    window.open(waPool[idx], '_blank');
  }};
  
  window.openRandomTgGroup = function() {{
    if (!tgPool || tgPool.length === 0) return;
    const idx = Math.floor(Math.random() * tgPool.length);
    window.open(tgPool[idx], '_blank');
  }};
}})();
</script>
"""

    # Optional random button
    random_button_html = ""
    random_btn_text = config.get("random_btn_text")
    random_btn_url = config.get("random_btn_url")
    if random_btn_text and random_btn_url:
        random_button_html = f'<div style="text-align: center; margin: 15px 0;"><a href="{random_btn_url}" target="_blank" style="display: inline-block; background-color: #6f42c1; color: white; text-decoration: none; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">{random_btn_text}</a></div>'

    html = f"""
<div style="font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 650px; margin: 15px auto; padding: 10px; background-color: #ffffff; color: #333333;">
  
  <!-- SEO/Feed Thumbnail Image -->
  <div style="text-align: center; margin-bottom: 20px;">
    <img src="{thumbnail_url}" alt="{match_name} Live Stream Preview" style="width: 100%; max-width: 600px; height: auto; border: 1px solid #cccccc; border-radius: 4px;" />
  </div>

  <!-- Ad Slot Top -->
  <div style="margin: 15px 0; text-align: center;">
    {ad_top}
  </div>

  <!-- Classic Goforsports Style Table -->
  <table border="0" cellpadding="0" cellspacing="0" style="background-color: white; border-collapse: collapse; border-spacing: 0px; border: 0.8pt solid rgb(0, 0, 0); color: black; line-height: 1.5; margin: 0px 0px 1.25rem; padding: 0px; text-align: center; vertical-align: baseline; width: 100%;">
    <tbody style="border: 0px; margin: 0px; padding: 0px; vertical-align: baseline;">
      <tr style="border: 0px; height: 50px; margin: 0px; padding: 0px; vertical-align: middle;">
        <td colspan="2" style="background: rgb(0, 102, 0); border: 0.7pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 100%;">
          <span style="color: white; font-size: large; font-weight: bold; text-transform: uppercase;">{team1} vs {team2}</span>
        </td>
      </tr>
      <tr style="background: rgb(0, 0, 0); border: 0px; height: 50px; margin: 1px; padding: 0px; vertical-align: middle;">
        <td colspan="1" style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;">
          <span style="color: white; font-weight: bold;">MATCH</span>
        </td>
        <td colspan="1" style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;">
          <span style="color: white; font-weight: bold;">SCHEDULE</span>
        </td>
      </tr>
      <tr style="border: 0px; height: 50px; margin: 1px; padding: 0px; vertical-align: middle;">
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; font-weight: bold; width: 50%;">MATCH</td>
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;">{team1} vs {team2}</td>
      </tr>
      <tr style="border: 0px; height: 50px; margin: 1px; padding: 0px; vertical-align: middle;">
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; font-weight: bold; width: 50%;">KICKOFF TIME</td>
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;" id="table-time-{safe_name}">Loading...</td>
      </tr>
      <tr style="border: 0px; height: 50px; margin: 1px; padding: 0px; vertical-align: middle;">
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; font-weight: bold; width: 50%;">QUALITY</td>
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;">1080p Full HD</td>
      </tr>
      <tr style="border: 0px; height: 50px; margin: 1px; padding: 0px; vertical-align: middle;">
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; font-weight: bold; width: 50%;">CHANNELS</td>
        <td style="border: 1pt solid black; height: 50px; padding: 2px; vertical-align: middle; width: 50%;">Live Multi-Link Streams</td>
      </tr>
    </tbody>
  </table>

  <!-- Ticking Countdown Box -->
  <div style="background: #f7f9fa; border: 1px solid #e1e8ed; border-radius: 6px; padding: 15px; text-align: center; margin-bottom: 25px;">
    <div style="font-size: 11px; text-transform: uppercase; color: #555555; letter-spacing: 1.5px; margin-bottom: 8px; font-weight: bold;">LIVE STREAM STARTS IN</div>
    <div style="display: flex; justify-content: center; gap: 12px;">
      <div style="min-width: 45px;"><span id="days-{safe_name}" style="font-size: 22px; font-weight: bold; color: rgb(0, 102, 0);">00</span><div style="font-size: 9px; text-transform: uppercase; color: #777777;">Days</div></div>
      <div style="min-width: 45px;"><span id="hours-{safe_name}" style="font-size: 22px; font-weight: bold; color: rgb(0, 102, 0);">00</span><div style="font-size: 9px; text-transform: uppercase; color: #777777;">Hrs</div></div>
      <div style="min-width: 45px;"><span id="mins-{safe_name}" style="font-size: 22px; font-weight: bold; color: rgb(0, 102, 0);">00</span><div style="font-size: 9px; text-transform: uppercase; color: #777777;">Mins</div></div>
      <div style="min-width: 45px;"><span id="secs-{safe_name}" style="font-size: 22px; font-weight: bold; color: rgb(0, 102, 0);">00</span><div style="font-size: 9px; text-transform: uppercase; color: #777777;">Secs</div></div>
    </div>
  </div>

  <!-- CTA Watch Button -->
  <div style="text-align: center; margin-bottom: 25px;">
    <a href="{page_url}" style="display: inline-block; width: 100%; max-width: 550px; box-sizing: border-box; background: linear-gradient(180deg, #28a745 0%, #218838 100%); color: #ffffff; text-decoration: none; padding: 15px 20px; border-radius: 6px; font-size: 16px; font-weight: bold; border: 1px solid #1e7e34; text-transform: uppercase; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
      CLICK HERE TO WATCH LIVE STREAM
    </a>
  </div>

  {social_buttons_html}
  {random_button_html}

  <!-- Match Preview Content -->
  <div style="line-height: 1.6; font-size: 14px; color: #333333; margin-bottom: 25px; border-top: 1px solid #eeeeee; padding-top: 20px;">
    <h3 style="color: #111111; font-size: 16px; font-weight: bold; margin-top: 0; margin-bottom: 12px; border-left: 4px solid rgb(0, 102, 0); padding-left: 8px;">Match Analysis & Overview</h3>
    <p style="margin: 0 0 15px 0;">
      As <strong>{team1}</strong> prepares to clash with <strong>{team2}</strong>, sports analysts and football fans around the world are gearing up for a blockbuster kickoff. Both squads have shown outstanding tactical growth and consistency in their recent matchups, making this a pivotal meeting that could decide tournament standing.
    </p>
    <p style="margin: 0 0 15px 0;">
      {team1} will look to maximize their field advantage and control the tempo from the midfield. Meanwhile, {team2} boasts quick transition play and high conversion rates on counter-attacks, which will present a major test for the host's defensive backline. Expect a high-stakes, fast-paced match with tactical modifications from both managers.
    </p>
  </div>

  <!-- Ad Slot Bottom -->
  <div style="margin: 15px 0; text-align: center;">
    {ad_bottom}
  </div>

</div>

{social_script}

<script type="text/javascript">
(function() {{
  const matchSafe = "{safe_name}";
  const kickoff = new Date("{match_time_str}").getTime();
  
  const tblDisplay = document.getElementById("table-time-" + matchSafe);
  
  const options = {{ weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' }};
  const localFormatted = new Date("{match_time_str}").toLocaleDateString(undefined, options);
  
  if (tblDisplay) tblDisplay.textContent = localFormatted;

  const elDays = document.getElementById("days-" + matchSafe);
  const elHrs = document.getElementById("hours-" + matchSafe);
  const elMins = document.getElementById("mins-" + matchSafe);
  const elSecs = document.getElementById("secs-" + matchSafe);

  const t = setInterval(function() {{
    const diff = kickoff - new Date().getTime();
    if (diff <= 0) {{
      clearInterval(t);
      return;
    }}
    if (elDays) elDays.textContent = String(Math.floor(diff / (1000 * 60 * 60 * 24))).padStart(2, '0');
    if (elHrs) elHrs.textContent = String(Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60))).padStart(2, '0');
    if (elMins) elMins.textContent = String(Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60))).padStart(2, '0');
    if (elSecs) elSecs.textContent = String(Math.floor((diff % (1000 * 60)) / 1000)).padStart(2, '0');
  }}, 1000);
}})();
</script>
"""
    return html

def generate_page_html(config, match, safe_name):
    match_name = match["match_name"]
    widget_base = config.get("widget_base_url", "https://yourdomain.com/widget.html")
    widget_url = f"{widget_base}?match={requests.utils.quote(match_name)}"
    
    ad_top = config.get("ad_code_top") or DEFAULT_AD_TOP
    ad_bottom = config.get("ad_code_bottom") or DEFAULT_AD_POPUP

    # Randomized WhatsApp and Telegram group redirect buttons
    whatsapp_groups = config.get("whatsapp_groups", [])
    telegram_channels = config.get("telegram_channels", [])
    
    wa_json = json.dumps(whatsapp_groups)
    tg_json = json.dumps(telegram_channels)
    
    social_buttons_html = ""
    social_script = ""
    
    if whatsapp_groups or telegram_channels:
        social_buttons_html += '<div style="text-align: center; margin: 15px 0; display: flex; justify-content: center; gap: 12px; flex-wrap: wrap;">'
        if whatsapp_groups:
            social_buttons_html += '<button onclick="openRandomWaGroup()" style="display: inline-block; background-color: #25d366; color: white; border: none; cursor: pointer; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">Join WhatsApp Group</button>'
        if telegram_channels:
            social_buttons_html += '<button onclick="openRandomTgGroup()" style="display: inline-block; background-color: #0088cc; color: white; border: none; cursor: pointer; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">Join Telegram Channel</button>'
        social_buttons_html += '</div>'
        
        social_script = f"""
<script type="text/javascript">
(function() {{
  const waPool = {wa_json};
  const tgPool = {tg_json};
  
  window.openRandomWaGroup = function() {{
    if (!waPool || waPool.length === 0) return;
    const idx = Math.floor(Math.random() * waPool.length);
    window.open(waPool[idx], '_blank');
  }};
  
  window.openRandomTgGroup = function() {{
    if (!tgPool || tgPool.length === 0) return;
    const idx = Math.floor(Math.random() * tgPool.length);
    window.open(tgPool[idx], '_blank');
  }};
}})();
</script>
"""

    # Optional random button
    random_button_html = ""
    random_btn_text = config.get("random_btn_text")
    random_btn_url = config.get("random_btn_url")
    if random_btn_text and random_btn_url:
        random_button_html = f'<div style="text-align: center; margin: 15px 0;"><a href="{random_btn_url}" target="_blank" style="display: inline-block; background-color: #6f42c1; color: white; text-decoration: none; padding: 10px 18px; border-radius: 4px; font-weight: bold; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">{random_btn_text}</a></div>'

    html = f"""
<div style="font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 680px; margin: 10px auto; padding: 15px; background-color: #ffffff; color: #333333; border: 1px solid #dddddd; border-radius: 6px;">
  
  <div style="text-align: center; font-size: 20px; font-weight: bold; color: #111111; margin-bottom: 15px;">
    {match_name} - Live Stream
  </div>

  <!-- Ad Slot Top -->
  <div style="margin: 15px 0; text-align: center;">
    {ad_top}
  </div>

  <!-- Widget Container / Player Frame (Initial countdown widget) -->
  <div id="player-frame-container" style="background: #000; border-radius: 6px; overflow: hidden; border: 1px solid #cccccc; box-shadow: 0 4px 15px rgba(0,0,0,0.15);">
    <iframe src="{widget_url}" width="100%" height="480px" frameborder="0" allowfullscreen style="display: block; background: #000; border: none;"></iframe>
  </div>

  {social_buttons_html}
  {random_button_html}

  <!-- Ad Slot Bottom -->
  <div style="margin: 15px 0; text-align: center;">
    {ad_bottom}
  </div>

</div>

{social_script}
"""
    return html

def generate_post_html(config, match, safe_name=None):
    return render_preview_post(config, match)


def generate_page_html(config, match, safe_name=None):
    return render_streaming_page(config, match, state="upcoming")


def can_refresh_portal_content(match, scheduler_config, now):
    if match.get("new_blog_iframe_set"):
        return False
    try:
        match_time = parse_match_time(match["match_time"])
    except Exception:
        return True

    start_offset = int(scheduler_config.get("active_window_start_minutes", 15))
    end_hours = int(scheduler_config.get("active_window_end_hours", 3))
    run_start = match_time - timedelta(minutes=start_offset)
    run_end = match_time + timedelta(hours=end_hours)
    return not (run_start <= now <= run_end)


def portal_mode(match):
    return str(match.get("portal_mode") or "auto").strip().lower()


def is_manual_portal_match(match):
    return portal_mode(match) in ("manual", "skip", "disabled") or match.get("portal_managed") is False


def log_dry_run(message):
    print(f"[dry-run] {message}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Create/update portal Blogger preview posts and streaming pages safely.")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without creating, updating, saving schedule, or generating thumbnails.")
    parser.add_argument("--create-missing-only", action="store_true", help="Only create missing portal items. Existing Page/Post content is not refreshed. This is the default.")
    parser.add_argument("--refresh-existing", action="store_true", help="Refresh existing auto-managed portal Page/Post content.")
    args = parser.parse_args()

    if args.create_missing_only and args.refresh_existing:
        print("[-] Choose either --create-missing-only or --refresh-existing, not both.")
        sys.exit(2)

    refresh_existing = bool(args.refresh_existing)

    print("[*] Starting Blogger precreate (Posts & Pages) pipeline...")
    if args.dry_run:
        print("[*] Dry run enabled: no Blogger writes, thumbnail writes, or schedule saves will be performed.")
    if refresh_existing:
        print("[*] Existing auto-managed portal content may be refreshed.")
    else:
        print("[*] Safe mode: existing portal content will be linked/recorded but not refreshed.")

    automation_config = load_automation_config()
    config = get_portal_blog_config(automation_config)
    scheduler_config = get_scheduler_config(automation_config)
    paths = ensure_runtime_dirs(automation_config)

    if not has_oauth(config):
        print("[-] Portal Blogger OAuth is missing. Configure portal_blog in master_config.json or run auth_blogger.py for new_blogger_config.json.")
        sys.exit(1)

    schedule = load_schedule(automation_config)
    if not schedule:
        print("[-] Match schedule empty.")
        sys.exit(1)

    print("[*] Fetching access token...")
    try:
        access_token = get_access_token(config)
    except Exception as e:
        print(f"[-] Access token error: {e}")
        sys.exit(1)
        
    print("[+] OAuth token verified.")
    changed = False
    now = datetime.now(timezone.utc)
    if args.dry_run:
        archived = []
    else:
        schedule, archived = archive_completed_matches(schedule, automation_config, scheduler_config, now)
        if archived:
            print(f"[*] Archived {len(archived)} completed match(es) out of the active schedule.")
            changed = True

    for match in schedule:
        if match.get("status") == "completed":
            continue

        if match.get("status", "pending") in ("pending", "processing", "active", "live"):
            if is_manual_portal_match(match):
                print(f"\n[*] Skipping manual portal match: {match['match_name']}")
                continue

            match["match_key"] = match.get("match_key") or match_key(match)
            safe_name = slugify_match_name(match["match_name"])
            can_refresh = can_refresh_portal_content(match, scheduler_config, now)
            
            # Step 1: Create or Refresh Blogger PAGE (Stream Player Page)
            page_title = streaming_page_title(match)
            page_html = generate_page_html(config, match, safe_name)
            page_id = match.get("new_blogger_page_id")
            
            if not page_id:
                print(f"\n[*] Checking stream Page for: {match['match_name']}...")
                try:
                    existing_page_id, existing_page_url = find_existing_blogger_page(config, access_token, page_title, match["match_name"])
                    if existing_page_id:
                        print(f"[*] Found existing stream Page. ID: {existing_page_id}")
                        page_id = existing_page_id
                        page_url = existing_page_url
                        if refresh_existing and can_refresh:
                            if args.dry_run:
                                log_dry_run(f"Would refresh existing stream Page for {match['match_name']} ({page_id}).")
                                changed = True
                            else:
                                page_url = update_blogger_page(config, access_token, page_id, page_title, page_html)
                        else:
                            print("[*] Existing stream Page content left unchanged.")
                    else:
                        if args.dry_run:
                            log_dry_run(f"Would create stream Page for {match['match_name']}.")
                            changed = True
                            page_id, page_url = "", ""
                        else:
                            page_id, page_url = create_blogger_page(config, access_token, page_title, page_html)
                    if page_id and not args.dry_run:
                        match["new_blogger_page_id"] = page_id
                        match["new_blogger_page_url"] = page_url
                        match["new_blog_iframe_set"] = False
                        match["new_blog_prepare_set"] = False
                        changed = True
                    elif page_id:
                        log_dry_run(f"Would record stream Page ID/URL for {match['match_name']}: {page_id} | {page_url}")
                        changed = True
                    if page_id:
                        print(f"[+] Page ready. ID: {page_id} | URL: {page_url}")
                except Exception as e:
                    print(f"[-] Page creation failed: {e}")
                    continue
            elif not can_refresh:
                print(f"\n[*] Skipping stream Page refresh for active/live match: {match['match_name']}")
            elif not refresh_existing:
                print(f"\n[*] Existing stream Page left unchanged for: {match['match_name']} (ID: {page_id})")
            else:
                print(f"\n[*] Refreshing existing stream Page for: {match['match_name']} (ID: {page_id})...")
                try:
                    if args.dry_run:
                        log_dry_run(f"Would refresh stream Page for {match['match_name']} ({page_id}).")
                        changed = True
                    else:
                        page_url = update_blogger_page(config, access_token, page_id, page_title, page_html)
                        match["new_blogger_page_url"] = page_url
                        match["new_blog_iframe_set"] = False
                        match["new_blog_prepare_set"] = False
                        print(f"[+] Page content refreshed. URL: {page_url}")
                        changed = True
                except Exception as e:
                    print(f"[-] Page refresh failed: {e}")

            # Step 2: Generate Match-Specific Thumbnail image
            img_filename = f"thumb_{safe_name}.jpg"
            img_path = os.path.join(paths["thumbnails_dir"], img_filename)
            if not os.path.exists(img_path):
                print(f"[*] Generating custom thumbnail for: {match['match_name']}...")
                if args.dry_run:
                    log_dry_run(f"Would generate thumbnail: {img_path}")
                    changed = True
                else:
                    t1, t2 = split_teams(match["match_name"])
                    generate_thumbnail(
                        t1,
                        t2,
                        match["match_time"],
                        img_path,
                        league=match.get("league") or match.get("competition") or config.get("default_league", ""),
                        channel=get_channel_info(match, config),
                    )

            # Step 3: Create or Refresh Blogger POST (Preview Post)
            post_title = preview_post_title(match)
            post_html = generate_post_html(config, match, safe_name)
            post_id = match.get("new_blogger_post_id")
            
            if not post_id:
                print(f"[*] Checking preview Post for: {match['match_name']}...")
                try:
                    existing_post_id, existing_post_url = find_existing_blogger_post(config, access_token, post_title, match["match_name"])
                    if existing_post_id:
                        print(f"[*] Found existing preview Post. ID: {existing_post_id}")
                        post_id = existing_post_id
                        post_url = existing_post_url
                        if refresh_existing and can_refresh:
                            if args.dry_run:
                                log_dry_run(f"Would refresh existing preview Post for {match['match_name']} ({post_id}).")
                                changed = True
                            else:
                                post_url = update_blogger_post(config, access_token, post_id, post_title, post_html)
                        else:
                            print("[*] Existing preview Post content left unchanged.")
                    else:
                        if args.dry_run:
                            log_dry_run(f"Would create preview Post for {match['match_name']}.")
                            changed = True
                            post_id, post_url = "", ""
                        else:
                            post_id, post_url = create_blogger_post(config, access_token, post_title, post_html)
                    if post_id and not args.dry_run:
                        match["new_blogger_post_id"] = post_id
                        match["new_blogger_post_url"] = post_url
                        changed = True
                    elif post_id:
                        log_dry_run(f"Would record preview Post ID/URL for {match['match_name']}: {post_id} | {post_url}")
                        changed = True
                    if post_id:
                        print(f"[+] Post ready. ID: {post_id} | URL: {post_url}")
                except Exception as e:
                    print(f"[-] Post creation failed: {e}")
            elif not can_refresh:
                print(f"[*] Skipping preview Post refresh for active/live match: {match['match_name']}")
            elif not refresh_existing:
                print(f"[*] Existing preview Post left unchanged for: {match['match_name']} (ID: {post_id})")
            else:
                print(f"[*] Refreshing existing preview Post for: {match['match_name']} (ID: {post_id})...")
                try:
                    if args.dry_run:
                        log_dry_run(f"Would refresh preview Post for {match['match_name']} ({post_id}).")
                        changed = True
                    else:
                        post_url = update_blogger_post(config, access_token, post_id, post_title, post_html)
                        match["new_blogger_post_url"] = post_url
                        print(f"[+] Post content refreshed. URL: {post_url}")
                        changed = True
                except Exception as e:
                    print(f"[-] Post refresh failed: {e}")

    if changed and not args.dry_run:
        save_schedule(schedule, automation_config)
        print("\n[+] Done. Schedule file updated with daily Page and Post details.")
    elif changed and args.dry_run:
        print("\n[+] Dry run complete. Schedule was not saved.")
    else:
        print("\n[+] No actions needed. All preview posts/pages are up to date.")

if __name__ == "__main__":
    main()
