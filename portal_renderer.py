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


def async_external_scripts(html_content):
    if not html_content:
        return ""

    def add_async(match):
        attrs = match.group(1) or ""
        if not re.search(r"\ssrc\s*=", attrs, flags=re.IGNORECASE):
            return match.group(0)
        if re.search(r"\s(async|defer)(\s|=|>|$)", attrs, flags=re.IGNORECASE):
            return match.group(0)
        return f"<script async{attrs}></script>"

    return re.sub(
        r"<script\b([^>]*)>\s*</script>",
        add_async,
        str(html_content),
        flags=re.IGNORECASE,
    )


def render_popup_ad(ad_content, delay_ms=3000):
    return f"""
<div id="popup-ad-overlay" style="align-items: center; background: rgba(0, 0, 0, 0.6); display: none; height: 100%; justify-content: center; left: 0; position: fixed; top: 0; width: 100%; z-index: 99999;">
  <div style="background: #ffffff; border-radius: 8px; padding: 10px; position: relative;">
    <button onclick="document.getElementById('popup-ad-overlay').style.display='none'" style="background: #333333; border: none; color: white; cursor: pointer; font-size: 16px; height: 26px; line-height: 1; position: absolute; right: -12px; top: -12px; width: 26px; border-radius: 50%;">&times;</button>
    {async_external_scripts(ad_content)}
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


TEAM_DISPLAY_ALIASES = {
    "usa": "USA",
    "uae": "UAE",
    "uk": "UK",
    "psg": "PSG",
    "lsg": "LSG",
    "rr": "RR",
    "rcb": "RCB",
    "csk": "CSK",
    "mi": "MI",
    "kkr": "KKR",
}

TEAM_FULL_NAME_ALIASES = {
    "aus": "Australia",
    "austrlia": "Australia",
    "asutrlia": "Australia",
    "australia": "Australia",
    "bra": "Brazil",
    "brazil": "Brazil",
    "hai": "Haiti",
    "haiti": "Haiti",
    "mor": "Morocco",
    "moroco": "Morocco",
    "morocco": "Morocco",
    "qat": "Qatar",
    "qater": "Qatar",
    "qatar": "Qatar",
    "sco": "Scotland",
    "scot": "Scotland",
    "scotland": "Scotland",
    "swi": "Switzerland",
    "swiss": "Switzerland",
    "switz": "Switzerland",
    "switzerland": "Switzerland",
    "tur": "Turkey",
    "turk": "Turkey",
    "turkey": "Turkey",
    "turkiye": "Turkey",
    "mex": "Mexico",
    "mexico": "Mexico",
    "sou": "South Africa",
    "south africa": "South Africa",
    "southafrica": "South Africa",
    "usa": "USA",
}

DEFAULT_TEAM_TITLE_CODES = {
    "arsenal": "ARS",
    "argentina": "ARGENTINA",
    "australia": "AUS",
    "aus": "AUS",
    "barcelona": "FCB",
    "bel": "BEL",
    "belgium": "BEL",
    "bra": "BRAZIL",
    "brazil": "BRAZIL",
    "chelsea": "CHE",
    "england": "ENG",
    "esp": "ESP",
    "france": "FRA",
    "germany": "GER",
    "haiti": "HAI",
    "hai": "HAI",
    "juventus": "JUV",
    "liverpool": "LFC",
    "manchester city": "MNC",
    "man city": "MNC",
    "manchester united": "MNU",
    "man united": "MNU",
    "morocco": "MOR",
    "mor": "MOR",
    "netherlands": "NED",
    "ned": "NED",
    "paraguay": "PAR",
    "para": "PAR",
    "par": "PAR",
    "portugal": "PORTUGAL",
    "psg": "PSG",
    "qatar": "QAT",
    "qater": "QAT",
    "qat": "QAT",
    "real madrid": "RMA",
    "scotland": "SCO",
    "scot": "SCO",
    "sco": "SCO",
    "switzerland": "SWISS",
    "swiss": "SWISS",
    "switz": "SWISS",
    "swi": "SWISS",
    "spain": "ESP",
    "tottenham": "TOT",
    "tottenham hotspur": "TOT",
    "turk": "TUR",
    "turkey": "TUR",
    "turkiye": "TUR",
    "uruguay": "URU",
}


def normalize_team_key(value):
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return " ".join(words).strip()


def display_team_name(value):
    key = normalize_team_key(value)
    if key in TEAM_FULL_NAME_ALIASES:
        return TEAM_FULL_NAME_ALIASES[key]
    words = []
    for raw in re.findall(r"[A-Za-z0-9]+", str(value or "")):
        alias = TEAM_DISPLAY_ALIASES.get(raw.lower())
        if alias:
            words.append(alias)
        elif raw.isupper() and len(raw) <= 4:
            words.append(raw)
        else:
            words.append(raw[:1].upper() + raw[1:])
    return " ".join(words).strip() or str(value or "").strip()


def list_config_values(*values):
    result = []
    for value in values:
        if isinstance(value, list):
            result.extend(value)
        elif isinstance(value, str) and value.strip():
            result.append(value)
    return result


def get_prominent_team(config, match):
    config = config or {}
    team1, team2 = split_teams(match.get("match_name", ""))
    teams = [team1, team2]
    team_by_key = {normalize_team_key(team): team for team in teams if normalize_team_key(team)}

    explicit = normalize_info_value(
        match.get("prominent_team") or match.get("featured_team") or match.get("title_team")
    )
    explicit_key = normalize_team_key(explicit)
    if explicit_key in team_by_key:
        return display_team_name(team_by_key[explicit_key])
    if explicit:
        return display_team_name(explicit)

    page_url_team = prominent_team_from_page_url(match)
    if page_url_team:
        return display_team_name(page_url_team)

    priority = list_config_values(
        config.get("prominent_team_priority"),
        config.get("title_priority_teams"),
        config.get("priority_teams"),
    )
    for item in priority:
        priority_key = normalize_team_key(item)
        if priority_key in team_by_key:
            return display_team_name(team_by_key[priority_key])

    return display_team_name(team1)


def prominent_team_from_page_url(match):
    url = str(match.get("new_blogger_page_url") or "").strip()
    if not url:
        return ""
    stem = url.rsplit("/", 1)[-1]
    stem = re.sub(r"\.html?$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[-_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    match_vs = re.search(r"(.+?)\bvs\b.+", stem, flags=re.IGNORECASE)
    if match_vs:
        team = match_vs.group(1)
    else:
        team = stem
    team = re.sub(r"\b(?:live|streaming|stream|links|info|watch|free|online|hd)\b", " ", team, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", team).strip()


def get_team_title_code(config, team):
    config = config or {}
    code_map = dict(DEFAULT_TEAM_TITLE_CODES)
    for source in (
        config.get("team_title_codes"),
        config.get("page_title_team_codes"),
        config.get("title_team_codes"),
    ):
        if isinstance(source, dict):
            for key, value in source.items():
                code = str(value or "").strip().upper()
                if code:
                    code_map[normalize_team_key(key)] = code

    key = normalize_team_key(team)
    if key in code_map:
        return code_map[key]

    display = display_team_name(team)
    compact = re.sub(r"[^A-Za-z0-9]", "", display)
    if display.isupper() and 2 <= len(compact) <= 5:
        return compact
    parts = re.findall(r"[A-Za-z0-9]+", display)
    if len(parts) == 1 and len(parts[0]) <= 10:
        return parts[0].upper()
    acronym = "".join(part[0].upper() for part in parts if part)
    return acronym[:4] or compact.upper() or "MATCH"


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
    raw_match_name = match.get("match_name", "Live Match")
    raw_team1, raw_team2 = split_teams(raw_match_name)
    team1 = display_team_name(raw_team1)
    team2 = display_team_name(raw_team2)
    match_name = f"{team1} vs {team2}"
    safe_name = slugify_match_name(raw_match_name)
    explicit_thumbnail = normalize_info_value(match.get("thumbnail_url"))
    feed_thumbnail = normalize_info_value(match.get("feed_thumbnail_url"))
    image_base = (config.get("image_base_url") or "").rstrip("/")
    thumbnail_url = explicit_thumbnail or (f"{image_base}/thumb_{safe_name}.jpg" if image_base else "")
    structured_image_url = feed_thumbnail or (thumbnail_url if thumbnail_url.startswith("http") else "")
    return {
        "match_name": match_name,
        "raw_match_name": raw_match_name,
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
        "feed_thumbnail_url": feed_thumbnail,
        "structured_image_url": structured_image_url,
        "page_url": match.get("new_blogger_page_url") or "#"
    }


def get_match_genre(match, config=None):
    config = config or {}
    genre = get_first_info(
        match,
        ["match_genre", "genre", "league", "competition", "tournament"],
        config.get("default_match_genre") or config.get("default_league", "")
    )
    if normalize_team_key(genre) in ("", "live sports"):
        return ""
    return genre


def preview_post_title(match, config=None):
    ctx = get_match_context(config or {}, match)
    genre = get_match_genre(match, config or {})
    genre_part = f" {genre}" if genre else ""
    return f"{ctx['team1']} vs {ctx['team2']}{genre_part} Match Preview & Kickoff Time"


def streaming_page_title(match, config=None):
    ctx = get_match_context(config or {}, match)
    team = get_prominent_team(config or {}, match)
    team_code = get_team_title_code(config or {}, team)
    title_format = (config or {}).get("streaming_page_title_format") or "{team_code} INFO"
    competition = get_match_genre(match, config or {}) or ctx["league"]
    return (
        str(title_format)
        .replace("{match}", ctx["match_name"])
        .replace("{team1}", ctx["team1"])
        .replace("{team2}", ctx["team2"])
        .replace("{team}", team)
        .replace("{team_code}", team_code)
        .replace("{competition}", competition)
        .replace("{stage}", normalize_info_value(match.get("stage")))
        .replace("{group}", normalize_info_value(match.get("group")))
        .replace("{date}", ctx["date"])
        .strip()
        or f"{team_code} INFO"
    )


def streaming_page_url_seed_title(match, config=None):
    team1, team2 = split_teams(match.get("match_name", ""))
    title_format = (config or {}).get("streaming_page_url_seed_format") or "{team1} vs {team2} Live Streaming"
    return (
        str(title_format)
        .replace("{team1}", display_team_name(team1))
        .replace("{team2}", display_team_name(team2))
        .replace("{match}", display_team_name(match.get("match_name", "")))
        .strip()
        or f"{display_team_name(team1)} vs {display_team_name(team2)} Live Streaming"
    )


def ad_top(config):
    ads = config.get("ads") or {}
    return async_external_scripts(ads.get("top_300x250") or config.get("ad_code_top") or DEFAULT_AD_TOP)


def ad_bottom(config):
    ads = config.get("ads") or {}
    delay_ms = config.get("ad_popup_delay_ms") or 3000
    popup_content = ads.get("popup_300x250")

    if popup_content:
        bottom = render_popup_ad(popup_content, delay_ms)
    elif config.get("ad_code_bottom"):
        bottom = async_external_scripts(config.get("ad_code_bottom"))
    else:
        bottom = render_popup_ad(DEFAULT_AD_TOP, delay_ms)

    extras = []
    popunder = ads.get("popunder") or DEFAULT_AD_POPUNDER
    social_bar = ads.get("social_bar") or DEFAULT_AD_SOCIAL_BAR
    if popunder:
        extras.append(async_external_scripts(popunder))
    if social_bar:
        extras.append(async_external_scripts(social_bar))
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


def render_square_ad(config):
    ad_html = ad_top(config)
    if not ad_html:
        return ""
    return f"""
  <div style="text-align:center; margin:18px auto 10px; min-height:260px;">
    <div style="display:inline-block; width:300px; max-width:100%; min-height:250px; overflow:hidden;">
      {ad_html}
    </div>
  </div>
"""


def render_social_block(config):
    whatsapp_groups = config.get("whatsapp_groups") or []
    telegram_channels = config.get("telegram_channels") or []
    target = config.get("social_click_target") or "_blank"
    buttons = []
    if whatsapp_groups:
        buttons.append('<button type="button" onclick="openPortalSocialGroup(\'wa\')" style="display:inline-block; min-width:220px; background:linear-gradient(135deg,#00c853,#075E54); color:#ffffff; border:2px solid #ffffff; cursor:pointer; padding:12px 18px; border-radius:6px; font-weight:900; font-size:14px; letter-spacing:1px; text-transform:uppercase; animation:gfsButtonPulse 1.7s infinite;">Join WhatsApp Group</button>')
    if telegram_channels:
        buttons.append('<button type="button" onclick="openPortalSocialGroup(\'tg\')" style="display:inline-block; min-width:220px; background:linear-gradient(135deg,#00b0ff,#004dff); color:#ffffff; border:2px solid #ffffff; cursor:pointer; padding:12px 18px; border-radius:6px; font-weight:900; font-size:14px; letter-spacing:1px; text-transform:uppercase; animation:gfsButtonPulse 1.9s infinite;">Join Telegram Channel</button>')

    random_btn_text = config.get("random_btn_text")
    random_btn_url = config.get("random_btn_url")
    if random_btn_text and random_btn_url:
        buttons.append(f'<a href="{escape(str(random_btn_url), quote=True)}" target="_blank" rel="noopener" style="display:inline-block; min-width:220px; background:linear-gradient(135deg,#ff1744,#ffb300); color:#ffffff; text-decoration:none; padding:12px 18px; border-radius:6px; border:2px solid #ffffff; font-weight:900; font-size:14px; letter-spacing:1px; text-transform:uppercase; animation:gfsButtonPulse 1.6s infinite;">{escape(str(random_btn_text))}</a>')

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
        <td style="border:1px solid #000000; height:50px; padding:6px; vertical-align:middle; font-weight:bold; width:50%;">{escape(str(label))}</td>
        <td style="border:1px solid #000000; height:50px; padding:6px; vertical-align:middle; width:50%;">{escape(str(value))}</td>
      </tr>
"""


def render_match_table(ctx, include_channels=True, include_quality=True):
    channel_row = table_row("CHANNELS", ctx["channels"]) if include_channels else ""
    quality_row = table_row("QUALITY", ctx["quality"]) if include_quality else ""
    return f"""
  <table border="0" cellpadding="0" cellspacing="0" style="background-color:white; border-collapse:collapse; border-spacing:0; border:1px solid #000000; color:black; line-height:1.5; margin:0 0 1.25rem; padding:0; text-align:center; vertical-align:baseline; width:100%;">
    <tbody style="border:0; margin:0; padding:0; vertical-align:baseline;">
      <tr style="border:0; height:50px; margin:0; padding:0; vertical-align:middle;">
        <td colspan="2" style="background:#006600; border:1px solid #000000; height:50px; padding:4px; vertical-align:middle; width:100%;">
          <span style="color:white; font-size:large; font-weight:bold; text-transform:uppercase;">{escape(ctx["team1"])} vs {escape(ctx["team2"])}</span>
        </td>
      </tr>
      <tr style="background:#000000; border:0; height:50px; margin:1px; padding:0; vertical-align:middle;">
        <td style="border:1px solid #000000; height:50px; padding:4px; vertical-align:middle; width:50%;"><span style="color:white; font-weight:bold;">MATCH</span></td>
        <td style="border:1px solid #000000; height:50px; padding:4px; vertical-align:middle; width:50%;"><span style="color:white; font-weight:bold;">SCHEDULE</span></td>
      </tr>
      {table_row("MATCH", f"{ctx['team1']} vs {ctx['team2']}")}
      {table_row("DATE", ctx["date"])}
      {table_row("TIME", ctx["time"])}
      {table_row("VENUE", ctx["venue"])}
      {table_row("LEAGUE", ctx["league"])}
      {channel_row}
      {quality_row}
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


def default_channel_rows(ctx):
    update_text = "Updated 15 minutes prior to the match."
    rows = [
        ("Worldwide", update_text),
        ("United States", "FOX / FS1 / FuboTV, subject to broadcast availability."),
        ("United Kingdom", "BBC / ITV, subject to broadcast availability."),
        ("Canada", "TSN / CTV, subject to broadcast availability."),
        ("Australia", "SBS / SBS On Demand, subject to broadcast availability."),
        ("India", update_text),
    ]
    for team in (ctx["team1"], ctx["team2"]):
        if team and not any(normalize_team_key(team) == normalize_team_key(row[0]) for row in rows):
            rows.append((team, update_text))
    return rows


def configured_channel_rows(match, config, ctx):
    values = (
        match.get("channels_by_country")
        or match.get("channel_by_country")
        or match.get("broadcast_by_country")
        or match.get("tv_channels_by_country")
        or config.get("default_channels_by_country")
    )
    rows = []
    if isinstance(values, dict):
        rows.extend((str(country), normalize_info_value(channel)) for country, channel in values.items())
    elif isinstance(values, list):
        for item in values:
            if isinstance(item, dict):
                country = item.get("country") or item.get("region") or item.get("name")
                channel = item.get("channel") or item.get("channels") or item.get("value") or item.get("info")
                if country and channel:
                    rows.append((str(country), normalize_info_value(channel)))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                rows.append((str(item[0]), normalize_info_value(item[1])))
    return [(country, channel) for country, channel in rows if country and channel] or default_channel_rows(ctx)


def render_channel_country_table(ctx, match, config):
    rows = configured_channel_rows(match, config, ctx)
    row_html = "".join(table_row(country, channel) for country, channel in rows)
    return f"""
  <table border="0" cellpadding="0" cellspacing="0" style="background-color:white; border-collapse:collapse; border-spacing:0; border:1px solid #000000; color:black; line-height:1.5; margin:0; padding:0; text-align:center; vertical-align:baseline; width:100%;">
    <tbody style="border:0; margin:0; padding:0; vertical-align:baseline;">
      <tr style="border:0; height:50px; margin:0; padding:0; vertical-align:middle;">
        <td colspan="2" style="background:#006600; border:1px solid #000000; height:50px; padding:4px; vertical-align:middle; width:100%;">
          <span style="color:white; font-size:large; font-weight:bold; text-transform:uppercase;">{escape(ctx["team1"])} vs {escape(ctx["team2"])} CHANNEL INFO</span>
        </td>
      </tr>
      <tr style="background:#000000; border:0; height:50px; margin:1px; padding:0; vertical-align:middle;">
        <td style="border:1px solid #000000; height:50px; padding:4px; vertical-align:middle; width:50%;"><span style="color:white; font-weight:bold;">COUNTRY / REGION</span></td>
        <td style="border:1px solid #000000; height:50px; padding:4px; vertical-align:middle; width:50%;"><span style="color:white; font-weight:bold;">CHANNEL INFO</span></td>
      </tr>
      {row_html}
    </tbody>
  </table>
"""


def result_info(match):
    result = match.get("result") if isinstance(match.get("result"), dict) else {}
    if result.get("team1_score") is None or result.get("team2_score") is None:
        if result.get("status") == "final_unverified":
            return {"status": "final_unverified", "label": "Final Score", "text": "Final score is not verified yet."}
        return {}
    status = str(result.get("status") or "").lower()
    if status == "final":
        label = "Final Score"
    elif status == "live":
        label = "Live Score"
    else:
        label = "Score Update"
    return {
        "status": status,
        "label": label,
        "text": result.get("score_text") or f"{match.get('team1', '')} {result.get('team1_score')}-{result.get('team2_score')} {match.get('team2', '')}",
        "source_url": result.get("source_url") or "",
        "checked_at": result.get("checked_at") or "",
    }


def render_result_banner(ctx, match):
    info = result_info(match)
    if not info:
        return ""
    color = "#006600"
    bg = "#eef8ee"
    if info["status"] == "live":
        color = "#b00020"
        bg = "#fff0f0"
    elif info["status"] == "final_unverified":
        color = "#7a5200"
        bg = "#fff8e8"
    return f"""
  <div style="background:{bg}; border:1px solid {color}; border-radius:6px; color:{color}; font-weight:900; line-height:1.45; margin:10px 0; padding:12px; text-align:center;">
    <div style="font-size:12px; letter-spacing:.7px; text-transform:uppercase;">{escape(info["label"])}</div>
    <div style="font-size:20px; margin-top:4px;">{escape(info["text"])}</div>
  </div>
"""


def render_countdown(match, safe_name):
    if str(match.get("status") or "").lower() in ("completed", "ended"):
        return ""
    if result_info(match).get("status") in ("final", "final_unverified"):
        return ""
    kickoff = str(match.get("match_time") or "").strip()
    if not kickoff:
        return ""
    element_id = "gfs-countdown-" + re.sub(r"[^a-z0-9_-]+", "-", safe_name.lower()).strip("-")
    return f"""
  <div id="{escape(element_id, quote=True)}" data-kickoff="{escape(kickoff, quote=True)}" style="background:#fff7d6; border:1px solid #f4c430; border-radius:6px; color:#111111; font-weight:900; line-height:1.35; margin:0 0 12px; padding:10px 12px; text-align:center;">
    <span style="display:block; font-size:11px; letter-spacing:.7px; text-transform:uppercase;">Match Countdown</span>
    <span data-countdown-text="1" style="display:block; font-size:18px; margin-top:3px;">Loading countdown...</span>
  </div>
  <script type="text/javascript">
  (function() {{
    var box = document.getElementById({json.dumps(element_id)});
    if (!box) return;
    var target = new Date(box.getAttribute('data-kickoff')).getTime();
    var text = box.querySelector('[data-countdown-text]');
    function pad(value) {{ return value < 10 ? '0' + value : String(value); }}
    function tick() {{
      var diff = target - Date.now();
      if (!isFinite(diff)) {{
        text.textContent = 'Kickoff time updating';
        return;
      }}
      if (diff <= 0) {{
        text.textContent = 'Links activating now';
        box.style.background = '#eaffea';
        box.style.borderColor = '#00a651';
        box.style.color = '#005a20';
        return;
      }}
      var total = Math.floor(diff / 1000);
      var days = Math.floor(total / 86400);
      var hours = Math.floor((total % 86400) / 3600);
      var mins = Math.floor((total % 3600) / 60);
      var secs = total % 60;
      text.textContent = (days ? days + 'd ' : '') + pad(hours) + 'h ' + pad(mins) + 'm ' + pad(secs) + 's';
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


def is_usable_widget_url(value):
    value = str(value or "").strip()
    if not value:
        return False
    lowered = value.lower()
    if "yourdomain.com" in lowered or lowered.startswith(("widget.html", "./widget.html")):
        return False
    return lowered.startswith(("http://", "https://"))


def match_source_urls(match):
    value = match.get("source_url") or match.get("source_urls") or []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(url).strip() for url in value if str(url).strip()]
    return []


def default_match_info_iframe_url(config, match):
    if not config.get("default_match_info_iframe_enabled", False):
        return ""
    preferred_domains = config.get("default_match_info_iframe_domains") or [
        "90live.yallatvlive.com",
        "es.footem.in",
        "worldcup.epicsports.mobi",
        "worldcup.epicsports.co.in",
        "www.epicsports.in",
        "epicsports.in",
    ]
    blocked_markers = config.get("default_match_info_iframe_blocked_markers") or [
        "rd9sports",
        "riddlearena",
    ]
    sources = match_source_urls(match)

    def score_url(url):
        lowered = url.lower()
        if any(marker in lowered for marker in blocked_markers):
            return None
        score = 100
        for idx, domain in enumerate(preferred_domains):
            if domain.lower() in lowered:
                score = min(score, idx * 10)
        if "live-score-preview" in lowered:
            score -= 8
        elif "preview" in lowered:
            score -= 4
        return score

    scored = [(score_url(url), url) for url in sources if is_usable_widget_url(url)]
    scored = [(score, url) for score, url in scored if score is not None]
    if not scored:
        return ""
    return sorted(scored, key=lambda item: (item[0], item[1]))[0][1]


def replace_widget_placeholders(template, ctx, match, url_encode=False):
    values = {
        "match": ctx["match_name"],
        "team1": ctx["team1"],
        "team2": ctx["team2"],
        "kickoff": match.get("match_time", ""),
        "date": ctx["date"],
        "time": ctx["time"],
        "page_url": ctx["page_url"],
        "channel": ctx["channels"],
        "league": ctx["league"],
    }
    rendered = str(template or "")
    for key, value in values.items():
        replacement = quote(str(value)) if url_encode else str(value)
        rendered = rendered.replace("{" + key + "}", replacement)
    return rendered


def render_fixture_table_widget(config, match, ctx, state="upcoming"):
    return ""


def render_external_match_widget(config, match, ctx, state="upcoming"):
    return ""


def render_match_info_panel(config, match, ctx, state):
    return f"""
  <div style="border:1px solid #d8e4d8; background:#f8fff8; padding:14px; margin:12px 0; border-radius:6px;">
    <div style="font-weight:800; color:#006600; text-transform:uppercase; letter-spacing:.5px; margin-bottom:6px;">Broadcast Channel Info</div>
    <div style="color:#222222; line-height:1.6;">{escape(ctx["channels"])}</div>
  </div>
"""


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
        "description": f"{ctx['match_name']} match preview, kickoff time, channel info and coverage updates.",
        "competitor": [{"@type": "SportsTeam", "name": ctx["team1"]}, {"@type": "SportsTeam", "name": ctx["team2"]}]
    }
    if ctx["venue"] != "TBA":
        data["location"] = {"@type": "Place", "name": ctx["venue"]}
    if ctx["structured_image_url"]:
        data["image"] = [ctx["structured_image_url"]]
    return '<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False) + "</script>"


def render_portal_shell(config, match, inner_html, state="upcoming", hero_html=""):
    ctx = get_match_context(config, match)
    social_html, social_script = render_social_block(config)
    social_row = portal_row(social_html, padding="12px") if social_html else ""
    return f"""
{hero_html}
<table border="0" cellpadding="0" cellspacing="0" style="font-family:Ubuntu, Poppins, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background:#ffffff; border-collapse:collapse; border:1px solid #000000; color:#222222; margin:12px auto; max-width:760px; width:100%; box-sizing:border-box;">
  <tbody>
  {inner_html}
  {social_row}
  </tbody>
</table>
{ad_bottom(config)}
{social_script}
<style type="text/css">
@keyframes gfsButtonPulse {{
  0% {{ transform:translateY(0) scale(1); box-shadow:0 0 0 0 rgba(255, 203, 0, .65); }}
  50% {{ transform:translateY(-1px) scale(1.015); box-shadow:0 0 0 9px rgba(255, 203, 0, 0); }}
  100% {{ transform:translateY(0) scale(1); box-shadow:0 0 0 0 rgba(255, 203, 0, 0); }}
}}
</style>
{render_structured_data(ctx, match, state)}
"""


def portal_row(content, padding="10px", align="center", bg="#ffffff"):
    if not content:
        return ""
    return f"""
  <tr>
    <td style="background:{bg}; border:1px solid #000000; padding:{padding}; text-align:{align}; vertical-align:middle; box-sizing:border-box;">
      {content}
    </td>
  </tr>
"""


def portal_header_row(text, bg="#006600"):
    return portal_row(
        f'<span style="color:#ffffff; font-size:18px; font-weight:800; line-height:1.35; text-transform:uppercase;">{escape(str(text))}</span>',
        padding="9px",
        bg=bg,
    )


def portal_text_row(title, paragraphs):
    content = f"""
      <div style="color:#111111; font-size:17px; font-weight:800; margin:0 0 8px; text-align:left;">{escape(title)}</div>
      {''.join(f'<p style="color:#333333; font-size:15px; line-height:1.65; margin:0 0 10px; text-align:left;">{escape(str(paragraph))}</p>' for paragraph in paragraphs)}
    """
    return portal_row(content, padding="14px", align="left")


def render_lineup_table(ctx, match):
    lineup = match.get("lineups") if isinstance(match.get("lineups"), dict) else {}
    status = str(lineup.get("status") or "predicted").lower()
    header = "CONFIRMED LINEUP" if status == "confirmed" else "PREDICTED LINEUP"
    text = normalize_info_value(lineup.get("text"))
    text_html = render_lineup_paragraphs(text)
    if not text_html:
        text_html = '<p style="margin:6px 0;"><b>Lineup information will be updated when available.</b></p>'
    score = result_info(match)
    score_header = score.get("label", "Score Prediction").upper()
    score_text = score.get("text") or f"{ctx['team1']} vs {ctx['team2']} - Prediction will be updated close to kickoff."
    return f"""
  <table border="0" cellpadding="0" cellspacing="0" style="background-color:white; border-collapse:collapse; border:1px solid #000000; color:black; text-align:center; width:100%; margin:0;">
    <tbody>
      <tr><td colspan="2" style="background:#006600; border:1px solid #000000; padding:8px;"><span style="color:white; font-size:medium; font-weight:bold;">{header}</span></td></tr>
      <tr><td colspan="2" style="border:1px solid #000000; padding:14px; text-align:left; line-height:1.65; font-size:14px;">{text_html}</td></tr>
      <tr><td colspan="2" style="background:#006600; border:1px solid #000000; padding:8px;"><span style="color:white; font-size:medium; font-weight:bold;">{escape(score_header)}</span></td></tr>
      <tr><td colspan="2" style="border:1px solid #000000; padding:12px;"><b>{escape(score_text)}</b></td></tr>
    </tbody>
  </table>
"""


def render_lineup_paragraphs(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    pairs = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        lower = line.lower()
        if lower in ("possible lineups", "predicted lineups", "confirmed lineups", "lineups"):
            idx += 1
            continue
        match = re.match(
            r"^(.+?)\s+(possible|predicted|probable|confirmed|starting)\s+(?:starting\s+)?line\s*up:?\s*$|^(.+?)\s+(possible|predicted|probable|confirmed|starting)\s+(?:starting\s+)?lineup:?\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            team = (match.group(1) or match.group(3) or "").strip()
            label = re.sub(r"\s+", " ", line).strip()
            lineup = ""
            if idx + 1 < len(lines):
                next_line = lines[idx + 1]
                if not re.search(r"\b(lineup|line up)\b", next_line, flags=re.IGNORECASE):
                    lineup = next_line
                    idx += 1
            if team and lineup:
                pairs.append((team, label, lineup))
            idx += 1
            continue
        idx += 1

    if pairs:
        return "".join(
            f'<p style="margin:6px 0 12px; line-height:1.65;"><b>{escape(display_team_name(team))}</b><br /><b>{escape(lineup)}</b></p>'
            for team, _label, lineup in pairs
        )

    return "".join(
        f'<p style="margin:6px 0 10px; line-height:1.65;"><b>{escape(line)}</b></p>'
        for line in lines
    )


def render_preview_post(config, match):
    ctx = get_match_context(config, match)
    image_html = render_thumbnail_image(ctx)
    jump_break = '<a name="more"></a>'
    hero_html = image_html + f"\n{jump_break}\n" if image_html else f"{jump_break}\n"
    cta_html = f"""
    <a href="{escape(ctx["page_url"], quote=True)}" style="display:inline-flex; align-items:center; justify-content:center; width:100%; min-height:62px; max-width:590px; box-sizing:border-box; background:linear-gradient(135deg,#ff1744 0%,#ffb300 48%,#00a86b 100%); color:#ffffff; text-decoration:none; padding:18px 20px; border-radius:6px; border:2px solid #ffffff; font-size:17px; font-weight:900; letter-spacing:1px; text-transform:uppercase; text-shadow:0 1px 2px rgba(0,0,0,.35); animation:gfsButtonPulse 1.45s infinite;">Click Here For Match Info</a>
"""
    body = f"""
  {portal_row(render_match_table(ctx, include_channels=False), padding="0")}
  {portal_row(render_result_banner(ctx, match), padding="10px")}
  {portal_row(render_lineup_table(ctx, match), padding="0")}
  {portal_text_row("Match Preview", [
      f"{ctx['team1']} vs {ctx['team2']} is scheduled for {ctx['date']} at {ctx['time']}, bringing together two sides with very different strengths.",
      f"{ctx['team1']} will look to control the tempo and create chances through quick attacking phases, while {ctx['team2']} can stay dangerous with compact defending, fast transitions and set-piece pressure.",
      "The final channel details and live coverage buttons are handled on the dedicated match page and are updated close to kickoff."
  ])}
  {portal_row(render_square_ad(config), padding="12px")}
  {portal_header_row("Match Page", bg="#006600")}
  {portal_row(cta_html, padding="22px 14px")}
  {portal_row(render_smartlink_button(config), padding="12px")}
"""
    return render_portal_shell(config, match, body, "upcoming", hero_html=hero_html)


def render_thumbnail_image(ctx):
    feed_url = ctx.get("feed_thumbnail_url") or ""
    thumbnail_url = ctx.get("thumbnail_url") or ""
    if not thumbnail_url and not feed_url:
        return ""
    hidden_feed = ""
    if feed_url and feed_url != thumbnail_url:
        hidden_feed = f'<img src="{escape(feed_url, quote=True)}" alt="{escape(ctx["match_name"], quote=True)} preview thumbnail" style="display:none !important; width:1px; height:1px; opacity:0;" />'
    visible = ""
    if thumbnail_url:
        visible = f'<img src="{escape(thumbnail_url, quote=True)}" alt="{escape(ctx["match_name"], quote=True)} live stream preview" style="width:100%; max-width:760px; height:auto; border:0; display:block; margin:0 auto;" />'
    return f"""
  <div style="text-align:center; margin:12px auto 10px; max-width:760px;">
    {hidden_feed}
    {visible}
  </div>
"""


def render_page_hero_image(config, match, ctx):
    page_url = normalize_info_value(
        match.get("page_hero_image_url")
        or match.get("page_thumbnail_url")
        or config.get("page_hero_image_url")
        or config.get("world_cup_page_hero_image_url")
    )
    if page_url:
        page_ctx = dict(ctx, thumbnail_url=page_url, feed_thumbnail_url="")
        return render_thumbnail_image(page_ctx)
    return render_thumbnail_image(ctx)


def render_streaming_page(config, match, state="upcoming", links_html=""):
    ctx = get_match_context(config, match)
    image_html = render_page_hero_image(config, match, ctx)
    countdown_html = render_countdown(match, slugify_match_name(ctx["match_name"]))
    result_html = render_result_banner(ctx, match)
    link_notice = f"""
  <div style="background:#fff7d6; border:2px solid #f4c430; color:#111111; border-radius:6px; padding:13px 14px; margin:0 0 14px; text-align:center; font-weight:900; line-height:1.5; text-transform:uppercase;">
    {escape(ctx["team1"])} vs {escape(ctx["team2"])} match links are added 15 minutes prior to kickoff.
  </div>
"""
    if state == "live" and links_html:
        player_iframe = ""
        stream_block = f"""
  {player_iframe}
  <div id="links-container" style="background:#ffffff; border:1px solid #cccccc; border-radius:6px; padding:12px; margin:18px 0; text-align:center;">
    <div style="font-weight:800; color:#006600; text-transform:uppercase; letter-spacing:.7px; margin-bottom:10px;">Live Stream Links</div>
    <div style="background:#eaffea; border:2px solid #00a651; border-radius:6px; color:#005a20; font-weight:900; margin:0 auto 14px; max-width:620px; padding:12px; text-transform:uppercase;">Match links added. Try Link 1 first, then switch if needed.</div>
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
        ended_score = result_html or '<div style="font-weight:900;">Final score will be updated after verification.</div>'
        stream_block = f"""
  <div id="player-frame-container" style="background:#f5f5f5; border:1px solid #dddddd; border-radius:6px; padding:20px; margin:18px 0; text-align:center; color:#555555; font-weight:800;">
    Match coverage has ended.
    {ended_score}
  </div>
"""
    else:
        stream_block = f"""
  <div id="player-frame-container" style="background:#f7f9fa; border:1px solid #e1e8ed; border-radius:6px; padding:20px; margin:18px 0; text-align:center; color:#333333; font-weight:800;">
    {link_notice}
    Live streaming links will be updated 15 minutes prior to the match.
  </div>
"""

    body = f"""
  {portal_header_row("Match Coverage")}
  {portal_row(f'<div style="font-size:14px; line-height:1.6; color:#444444;">{escape(ctx["match_name"])} coverage page with broadcast information and streaming buttons when active.</div>', padding="10px")}
  {portal_row(render_channel_country_table(ctx, match, config), padding="0")}
  {portal_row(result_html, padding="10px")}
  {portal_row(render_square_ad(config), padding="12px")}
  {portal_header_row("Streaming Links", bg="#000000")}
  {portal_row(countdown_html + stream_block, padding="12px")}
  {render_page_lineup_section(ctx, match)}
  {portal_row(render_smartlink_button(config), padding="12px")}
"""
    return render_portal_shell(config, match, body, state, hero_html=image_html)


def render_page_lineup_section(ctx, match):
    lineup = match.get("lineups") if isinstance(match.get("lineups"), dict) else {}
    if str(lineup.get("status") or "").lower() != "confirmed":
        return ""
    return f"""
  {portal_header_row("Confirmed Lineup", bg="#006600")}
  {portal_row(render_lineup_table(ctx, match), padding="0")}
"""
