#!/usr/bin/env python3
import json
import re
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import quote

IST = timezone(timedelta(hours=5, minutes=30))

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
  <script type="text/javascript" src="https://throughalivemedication.com/26752c18ca8361bba098d31342583042/invoke.js"></script>
</div>
"""

DEFAULT_AD_POPUNDER = '<script type="text/javascript" src="https://throughalivemedication.com/45/96/a9/4596a9a27ac7c137dd494fd1f200edbb.js"></script>'
DEFAULT_AD_SOCIAL_BAR = '<script type="text/javascript" src="https://throughalivemedication.com/66/f1/17/66f11775fe2744312299821ac71b38f1.js"></script>'


def render_popup_ad(ad_content, delay_ms=3000):
    return f"""
<div id="popup-ad-overlay" style="align-items: center; background: rgba(0, 0, 0, 0.6); display: none; height: 100%; justify-content: center; left: 0; position: fixed; top: 0; width: 100%; z-index: 99999;">
  <div style="background: #ffffff; border-radius: 8px; padding: 10px; position: relative;">
    <button onclick="document.getElementById('popup-ad-overlay').style.display='none'" style="background: #333333; border: none; color: white; cursor: pointer; font-size: 16px; height: 26px; line-height: 1; position: absolute; right: -12px; top: -12px; width: 26px; border-radius: 50%;">&times;</button>
    {ad_content}
  </div>
</div>
<script type="text/javascript">
  window.addEventListener('load', function() {{
    setTimeout(function() {{
      var overlay = document.getElementById('popup-ad-overlay');
      if (overlay) overlay.style.display = 'flex';
    }}, {int(delay_ms)});
  }});
</script>
"""


DEFAULT_AD_POPUP = render_popup_ad(DEFAULT_AD_TOP) + "\n" + DEFAULT_AD_POPUNDER + "\n" + DEFAULT_AD_SOCIAL_BAR


def slugify_match_name(match_name):
    slug = re.sub(r"[^a-z0-9]+", "_", (match_name or "").lower()).strip("_")
    return slug or "match"


def split_teams(match_name):
    parts = re.split(r"(?:\s+Vs\s+|\s+vs\s+|\s+VS\s+|\s+v\s+)", match_name or "", maxsplit=1)
    team1 = parts[0].strip() if parts and parts[0].strip() else "Team A"
    team2 = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "Team B"
    return team1, team2


def parse_match_time(value):
    value = str(value or "").strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_match_date(value):
    try:
        return parse_match_time(value).astimezone(IST).strftime("%d-%b-%Y")
    except Exception:
        return str(value or "TBA")


def format_match_time(value):
    try:
        return parse_match_time(value).astimezone(IST).strftime("%I:%M %p IST").lstrip("0")
    except Exception:
        return str(value or "TBA")


def normalize_info_value(value):
    if isinstance(value, list):
        return ", ".join(normalize_info_value(item) for item in value if normalize_info_value(item))
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            item_value = normalize_info_value(item)
            if item_value:
                parts.append(f"{key}: {item_value}")
        return "; ".join(parts)
    return str(value or "").strip()


def get_first_info(match, keys, default="TBA"):
    for key in keys:
        value = normalize_info_value(match.get(key))
        if value:
            return value
    return default


def get_channel_info(match, config=None):
    channel = get_first_info(
        match,
        ["channels", "channel", "broadcast_channels", "tv_channels", "channel_info", "broadcaster"],
        ""
    )
    if channel:
        return channel
    if config:
        configured = normalize_info_value(config.get("default_channel_info"))
        if configured:
            return configured
    return "Channel details will be updated before kickoff."


def get_match_context(config, match):
    match_name = match.get("match_name", "Live Match")
    team1, team2 = split_teams(match_name)
    safe_name = slugify_match_name(match_name)
    image_base = (config.get("image_base_url") or "").rstrip("/")
    thumbnail_url = f"{image_base}/thumb_{safe_name}.jpg" if image_base else ""
    return {
        "match_name": match_name,
        "team1": team1,
        "team2": team2,
        "safe_name": safe_name,
        "date": format_match_date(match.get("match_time")),
        "time": format_match_time(match.get("match_time")),
        "venue": get_first_info(match, ["venue", "stadium", "ground"], "TBA"),
        "league": get_first_info(match, ["league", "competition", "tournament"], config.get("default_league", "Live Sports")),
        "quality": get_first_info(match, ["quality", "stream_quality"], config.get("default_quality", "HD / Auto Quality")),
        "channels": get_channel_info(match, config),
        "thumbnail_url": thumbnail_url,
        "page_url": match.get("new_blogger_page_url") or "#"
    }


def preview_post_title(match):
    ctx = get_match_context({}, match)
    return f"{ctx['team1']} vs {ctx['team2']} Live Stream, Preview, Kickoff Time & Channel Info"


def streaming_page_title(match):
    ctx = get_match_context({}, match)
    return f"{ctx['team1']} vs {ctx['team2']} Live Streaming Links"


def ad_top(config):
    ads = config.get("ads") or {}
    return ads.get("top_300x250") or config.get("ad_code_top") or DEFAULT_AD_TOP


def ad_bottom(config):
    ads = config.get("ads") or {}
    delay_ms = config.get("ad_popup_delay_ms") or 3000
    popup_content = ads.get("popup_300x250")

    if popup_content:
        bottom = render_popup_ad(popup_content, delay_ms)
    elif config.get("ad_code_bottom"):
        bottom = config.get("ad_code_bottom")
    else:
        bottom = render_popup_ad(DEFAULT_AD_TOP, delay_ms)

    extras = []
    popunder = ads.get("popunder") or DEFAULT_AD_POPUNDER
    social_bar = ads.get("social_bar") or DEFAULT_AD_SOCIAL_BAR
    if popunder:
        extras.append(popunder)
    if social_bar:
        extras.append(social_bar)
    return bottom + "\n" + "\n".join(extras)


def render_smartlink_button(config):
    smartlink = config.get("smartlink") or {}
    if smartlink.get("enabled") is False:
        return ""
    url = str(smartlink.get("url") or "").strip()
    if not url:
        return ""
    text = str(smartlink.get("text") or "Continue To Live Coverage").strip()
    return f"""
  <div style="text-align:center; margin:14px 0 22px;">
    <a href="{escape(url, quote=True)}" target="_blank" rel="noopener" style="display:inline-block; width:100%; max-width:560px; box-sizing:border-box; background:#f4c430; color:#111111; text-decoration:none; padding:13px 18px; border-radius:5px; border:1px solid #b98900; font-weight:900; letter-spacing:.7px; text-transform:uppercase; box-shadow:0 0 0 rgba(244,196,48,.55); animation:portalSmartPulse 1.8s infinite;">{escape(text)}</a>
  </div>
  <style>@keyframes portalSmartPulse {{ 0% {{ box-shadow:0 0 0 0 rgba(244,196,48,.55); }} 70% {{ box-shadow:0 0 0 10px rgba(244,196,48,0); }} 100% {{ box-shadow:0 0 0 0 rgba(244,196,48,0); }} }}</style>
"""


def render_social_block(config):
    whatsapp_groups = config.get("whatsapp_groups") or []
    telegram_channels = config.get("telegram_channels") or []
    target = config.get("social_click_target") or "_blank"
    buttons = []
    if whatsapp_groups:
        buttons.append('<button type="button" onclick="openPortalSocialGroup(\'wa\')" style="display:inline-block; min-width:220px; background:#075E54; color:#ffffff; border:0; cursor:pointer; padding:12px 18px; border-radius:5px; font-weight:700; font-size:14px; letter-spacing:1px; text-transform:uppercase;">Join WhatsApp Group</button>')
    if telegram_channels:
        buttons.append('<button type="button" onclick="openPortalSocialGroup(\'tg\')" style="display:inline-block; min-width:220px; background:#0088cc; color:#ffffff; border:0; cursor:pointer; padding:12px 18px; border-radius:5px; font-weight:700; font-size:14px; letter-spacing:1px; text-transform:uppercase;">Join Telegram Channel</button>')

    random_btn_text = config.get("random_btn_text")
    random_btn_url = config.get("random_btn_url")
    if random_btn_text and random_btn_url:
        buttons.append(f'<a href="{escape(str(random_btn_url), quote=True)}" target="_blank" rel="noopener" style="display:inline-block; min-width:220px; background:#107821; color:#ffffff; text-decoration:none; padding:12px 18px; border-radius:5px; font-weight:700; font-size:14px; letter-spacing:1px; text-transform:uppercase;">{escape(str(random_btn_text))}</a>')

    if not buttons:
        return "", ""

    html = '<div style="text-align:center; margin:18px 0; display:flex; justify-content:center; gap:12px; flex-wrap:wrap;">' + "".join(buttons) + "</div>"
    script = f"""
<script type="text/javascript">
(function() {{
  var portalSocialPools = {{
    wa: {json.dumps(whatsapp_groups)},
    tg: {json.dumps(telegram_channels)}
  }};
  window.openPortalSocialGroup = function(kind) {{
    var pool = portalSocialPools[kind] || [];
    if (!pool.length) return;
    var url = pool[Math.floor(Math.random() * pool.length)];
    window.open(url, {json.dumps(target)}, 'noopener');
  }};
}})();
</script>
"""
    return html, script


def table_row(label, value):
    return f"""
      <tr style="border:0; height:50px; margin:1px; padding:0; vertical-align:middle;">
        <td style="border:1pt solid black; height:50px; padding:6px; vertical-align:middle; font-weight:bold; width:50%;">{escape(str(label))}</td>
        <td style="border:1pt solid black; height:50px; padding:6px; vertical-align:middle; width:50%;">{escape(str(value))}</td>
      </tr>
"""


def render_match_table(ctx):
    return f"""
  <table border="0" cellpadding="0" cellspacing="0" style="background-color:white; border-collapse:collapse; border-spacing:0; border:0.8pt solid #000000; color:black; line-height:1.5; margin:0 0 1.25rem; padding:0; text-align:center; vertical-align:baseline; width:100%;">
    <tbody style="border:0; margin:0; padding:0; vertical-align:baseline;">
      <tr style="border:0; height:50px; margin:0; padding:0; vertical-align:middle;">
        <td colspan="2" style="background:#006600; border:0.7pt solid black; height:50px; padding:4px; vertical-align:middle; width:100%;">
          <span style="color:white; font-size:large; font-weight:bold; text-transform:uppercase;">{escape(ctx["team1"])} vs {escape(ctx["team2"])}</span>
        </td>
      </tr>
      <tr style="background:#000000; border:0; height:50px; margin:1px; padding:0; vertical-align:middle;">
        <td style="border:1pt solid black; height:50px; padding:4px; vertical-align:middle; width:50%;"><span style="color:white; font-weight:bold;">MATCH</span></td>
        <td style="border:1pt solid black; height:50px; padding:4px; vertical-align:middle; width:50%;"><span style="color:white; font-weight:bold;">SCHEDULE</span></td>
      </tr>
      {table_row("MATCH", f"{ctx['team1']} vs {ctx['team2']}")}
      {table_row("DATE", ctx["date"])}
      {table_row("TIME", ctx["time"])}
      {table_row("VENUE", ctx["venue"])}
      {table_row("LEAGUE", ctx["league"])}
      {table_row("CHANNELS", ctx["channels"])}
      {table_row("QUALITY", ctx["quality"])}
    </tbody>
  </table>
"""


def render_channel_section(ctx):
    return f"""
  <div style="border:1px solid #d8e4d8; background:#f8fff8; padding:14px; margin:18px 0; border-radius:6px;">
    <div style="font-weight:800; color:#006600; text-transform:uppercase; letter-spacing:.5px; margin-bottom:6px;">Broadcast Channel Info</div>
    <div style="color:#222222; line-height:1.6;">{escape(ctx["channels"])}</div>
  </div>
"""


def render_countdown(match, safe_name):
    match_time = escape(str(match.get("match_time", "")), quote=True)
    return f"""
  <div style="background:#f7f9fa; border:1px solid #e1e8ed; border-radius:6px; padding:15px; text-align:center; margin:18px 0 24px;">
    <div style="font-size:11px; text-transform:uppercase; color:#555555; letter-spacing:1.5px; margin-bottom:8px; font-weight:bold;">Live stream starts in</div>
    <div style="display:flex; justify-content:center; gap:10px; flex-wrap:wrap;">
      <div style="min-width:55px;"><span id="days-{safe_name}" style="font-size:22px; font-weight:bold; color:#006600;">00</span><div style="font-size:9px; text-transform:uppercase; color:#777777;">Days</div></div>
      <div style="min-width:55px;"><span id="hours-{safe_name}" style="font-size:22px; font-weight:bold; color:#006600;">00</span><div style="font-size:9px; text-transform:uppercase; color:#777777;">Hrs</div></div>
      <div style="min-width:55px;"><span id="mins-{safe_name}" style="font-size:22px; font-weight:bold; color:#006600;">00</span><div style="font-size:9px; text-transform:uppercase; color:#777777;">Mins</div></div>
      <div style="min-width:55px;"><span id="secs-{safe_name}" style="font-size:22px; font-weight:bold; color:#006600;">00</span><div style="font-size:9px; text-transform:uppercase; color:#777777;">Secs</div></div>
    </div>
  </div>
  <script type="text/javascript">
  (function() {{
    var kickoff = new Date("{match_time}").getTime();
    var ids = ["days", "hours", "mins", "secs"].map(function(k) {{ return document.getElementById(k + "-{safe_name}"); }});
    function tick() {{
      var diff = kickoff - Date.now();
      if (diff <= 0) diff = 0;
      var days = Math.floor(diff / 86400000);
      var hours = Math.floor((diff % 86400000) / 3600000);
      var mins = Math.floor((diff % 3600000) / 60000);
      var secs = Math.floor((diff % 60000) / 1000);
      [days, hours, mins, secs].forEach(function(v, i) {{
        if (ids[i]) ids[i].textContent = String(v).padStart(2, "0");
      }});
    }}
    tick();
    setInterval(tick, 1000);
  }})();
  </script>
"""


def render_status_pill(state):
    labels = {
        "upcoming": ("Upcoming", "#006600", "#eef8ee"),
        "preparing": ("Preparing Links", "#b36b00", "#fff8e8"),
        "live": ("Live Now", "#b00020", "#fff0f0"),
        "ended": ("Match Ended", "#555555", "#f4f4f4")
    }
    text, color, bg = labels.get(state, labels["upcoming"])
    return f'<div style="display:inline-block; background:{bg}; color:{color}; border:1px solid {color}; padding:6px 12px; border-radius:4px; font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:.7px;">{text}</div>'


def build_widget_url(config, match):
    base = config.get("widget_base_url") or "widget.html"
    params = {
        "match": match.get("match_name", ""),
        "kickoff": match.get("match_time", ""),
        "page_url": match.get("new_blogger_page_url", ""),
        "channel": get_channel_info(match, config)
    }
    query = "&".join(f"{key}={quote(str(value))}" for key, value in params.items() if value)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{query}" if query else base


def render_structured_data(ctx, match, state="upcoming"):
    event_status = "https://schema.org/EventScheduled"
    if state == "live":
        event_status = "https://schema.org/EventInProgress"
    elif state == "ended":
        event_status = "https://schema.org/EventCompleted"
    data = {
        "@context": "https://schema.org",
        "@type": "SportsEvent",
        "name": ctx["match_name"],
        "startDate": match.get("match_time"),
        "eventStatus": event_status,
        "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode",
        "description": f"{ctx['match_name']} live stream, kickoff time, channel info and match preview.",
        "competitor": [{"@type": "SportsTeam", "name": ctx["team1"]}, {"@type": "SportsTeam", "name": ctx["team2"]}]
    }
    if ctx["venue"] != "TBA":
        data["location"] = {"@type": "Place", "name": ctx["venue"]}
    if ctx["thumbnail_url"]:
        data["image"] = [ctx["thumbnail_url"]]
    return '<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False) + "</script>"


def render_portal_shell(config, match, inner_html, state="upcoming"):
    ctx = get_match_context(config, match)
    social_html, social_script = render_social_block(config)
    return f"""
<div style="font-family: Ubuntu, Poppins, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width:760px; margin:12px auto; padding:12px; background:#ffffff; color:#222222; box-sizing:border-box;">
  <div style="text-align:center; margin:4px 0 14px;">{render_status_pill(state)}</div>
  {inner_html}
  {social_html}
  <div style="margin:18px 0; text-align:center;">{ad_bottom(config)}</div>
</div>
{social_script}
{render_structured_data(ctx, match, state)}
"""


def render_preview_post(config, match):
    ctx = get_match_context(config, match)
    image_html = ""
    if ctx["thumbnail_url"]:
        image_html = f"""
  <div style="text-align:center; margin-bottom:16px;">
    <img src="{escape(ctx["thumbnail_url"], quote=True)}" alt="{escape(ctx["match_name"], quote=True)} live stream preview" style="width:100%; max-width:680px; height:auto; border:1px solid #cccccc; border-radius:4px;" />
  </div>
"""
    body = f"""
  {image_html}
  <h2 style="font-size:22px; line-height:1.35; color:#111111; margin:6px 0 12px; text-align:center;">{escape(ctx["match_name"])} Live Stream, Preview and Channel Info</h2>
  <div style="margin:16px 0; text-align:center;">{ad_top(config)}</div>
  {render_match_table(ctx)}
  {render_countdown(match, ctx["safe_name"])}
  <div style="text-align:center; margin:18px 0 24px;">
    <a href="{escape(ctx["page_url"], quote=True)}" style="display:inline-block; width:100%; max-width:560px; box-sizing:border-box; background:#107821; color:#ffffff; text-decoration:none; padding:14px 18px; border-radius:5px; font-size:16px; font-weight:800; letter-spacing:1px; text-transform:uppercase;">Click Here To Watch Live Stream</a>
  </div>
  {render_channel_section(ctx)}
  <div style="line-height:1.65; font-size:15px; color:#333333; margin:20px 0; border-top:1px solid #eeeeee; padding-top:18px;">
    <h3 style="color:#111111; font-size:17px; font-weight:bold; margin:0 0 10px; border-left:4px solid #006600; padding-left:8px;">Match Preview</h3>
    <p style="margin:0 0 12px;">{escape(ctx["team1"])} and {escape(ctx["team2"])} meet in {escape(ctx["league"])} action, with kickoff scheduled for {escape(ctx["date"])} at {escape(ctx["time"])}. This page includes match timing, live stream access, channel information and updated streaming links when coverage begins.</p>
    <p style="margin:0 0 12px;">The streaming page will activate before kickoff. If the official broadcast or channel details change, this article and the match page can be refreshed from the automation schedule.</p>
  </div>
  <table border="0" cellpadding="0" cellspacing="0" style="background-color:white; border-collapse:collapse; border:0.8pt solid #000000; color:black; text-align:center; width:100%; margin:18px 0;">
    <tbody>
      <tr><td colspan="2" style="background:#006600; border:0.7pt solid black; padding:8px;"><span style="color:white; font-size:medium; font-weight:bold;">PREDICTED LINEUP</span></td></tr>
      <tr><td colspan="2" style="border:0.7pt solid black; padding:16px; text-align:left;"><b>Coming Soon...</b></td></tr>
      <tr><td colspan="2" style="background:#006600; border:0.7pt solid black; padding:8px;"><span style="color:white; font-size:medium; font-weight:bold;">SCORE PREDICTION</span></td></tr>
      <tr><td colspan="2" style="border:0.7pt solid black; padding:12px;"><b>{escape(ctx["team1"])} vs {escape(ctx["team2"])} - Prediction will be updated close to kickoff.</b></td></tr>
    </tbody>
  </table>
"""
    return render_portal_shell(config, match, body, "upcoming")


def render_streaming_page(config, match, state="upcoming", links_html=""):
    ctx = get_match_context(config, match)
    widget_url = build_widget_url(config, match)
    if state == "live" and links_html:
        stream_block = f"""
  <div id="player-frame-container" style="background:#ffffff; border:1px solid #cccccc; border-radius:6px; padding:12px; margin:18px 0; text-align:center;">
    <div style="font-weight:800; color:#006600; text-transform:uppercase; letter-spacing:.7px; margin-bottom:10px;">Live Stream Links</div>
    {links_html}
  </div>
"""
    elif state == "preparing":
        stream_block = """
  <div id="player-frame-container" style="background:#fff8e8; border:1px solid #e2b96a; border-radius:6px; padding:20px; margin:18px 0; text-align:center; color:#6f4600; font-weight:800;">
    Streaming links are being activated. Refresh this page shortly.
  </div>
"""
    elif state == "ended":
        stream_block = """
  <div id="player-frame-container" style="background:#f5f5f5; border:1px solid #dddddd; border-radius:6px; padding:20px; margin:18px 0; text-align:center; color:#555555; font-weight:800;">
    Match coverage has ended.
  </div>
"""
    else:
        stream_block = f"""
  <div id="player-frame-container" style="background:#000000; border-radius:6px; overflow:hidden; border:1px solid #cccccc; margin:18px 0;">
    <iframe src="{escape(widget_url, quote=True)}" width="100%" height="480" frameborder="0" allowfullscreen style="display:block; background:#000000; border:0;"></iframe>
  </div>
"""

    body = f"""
  <h2 style="font-size:22px; line-height:1.35; color:#111111; margin:6px 0 10px; text-align:center;">{escape(ctx["match_name"])} Live Streaming Page</h2>
  <p style="font-size:14px; line-height:1.6; color:#444444; text-align:center; margin:0 0 14px;">Watch page for {escape(ctx["match_name"])} with kickoff time, channel information, live status and stream buttons.</p>
  <div style="margin:16px 0; text-align:center;">{ad_top(config)}</div>
  {render_match_table(ctx)}
  {render_channel_section(ctx)}
  {stream_block}
  {render_smartlink_button(config)}
"""
    return render_portal_shell(config, match, body, state)
