#!/usr/bin/env python3
import re
from datetime import datetime, timedelta, timezone
from html import unescape

import requests


LINEUP_KEYWORDS = (
    "confirmed lineup",
    "confirmed line up",
    "starting lineup",
    "starting line up",
    "predicted lineup",
    "predicted line up",
    "probable lineup",
    "probable line up",
    "lineup",
    "line up",
)

CONFIRMED_KEYWORDS = (
    "confirmed lineup",
    "confirmed line up",
    "starting lineup",
    "starting line up",
)

STOP_KEYWORDS = (
    "match details",
    "match preview",
    "preview",
    "live stream",
    "streaming",
    "watch live",
    "score prediction",
    "prediction",
    "related posts",
    "advertisement",
    "join whatsapp",
    "telegram",
)


def source_urls_for_match(match):
    value = match.get("source_url", "")
    if isinstance(value, list):
        return [str(url).strip() for url in value if str(url).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def html_to_lines(html):
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|li|tr|h[1-6]|table)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text.replace("&nbsp;", " "))
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip(" -:\t")
        if 3 <= len(line) <= 220:
            lines.append(line)
    return lines


def extract_lineup_from_html(html):
    lines = html_to_lines(html)
    best = None

    for idx, line in enumerate(lines):
        lower = line.lower()
        if not any(keyword in lower for keyword in LINEUP_KEYWORDS):
            continue
        strong_trigger = any(
            keyword in lower
            for keyword in (
                "confirmed lineup",
                "confirmed line up",
                "possible lineup",
                "possible lineups",
                "possible starting lineup",
                "predicted lineup",
                "predicted line up",
                "probable lineup",
                "probable line up",
                "starting lineup",
                "starting line up",
            )
        )
        if not strong_trigger and "preview" in lower:
            continue

        block = [line]
        for next_line in lines[idx + 1:idx + 14]:
            next_lower = next_line.lower()
            if block and any(keyword in next_lower for keyword in STOP_KEYWORDS) and not any(
                keyword in next_lower for keyword in LINEUP_KEYWORDS
            ):
                break
            block.append(next_line)

        text = "\n".join(block).strip()
        if len(text) < 20:
            continue

        text_lower = text.lower()
        status = "confirmed" if any(keyword in text_lower for keyword in CONFIRMED_KEYWORDS) else "predicted"
        if any(keyword in text_lower for keyword in ("possible", "probable", "predicted")):
            status = "predicted"
        candidate = {"status": status, "text": text}
        if status == "confirmed":
            return candidate
        if not best:
            best = candidate

    return best


def should_refresh_lineups(match, scheduler_config, now, active=False):
    if scheduler_config.get("lineup_refresh_enabled") is False:
        return False
    interval_key = "lineup_active_refresh_interval_minutes" if active else "lineup_refresh_interval_minutes"
    interval = int(scheduler_config.get(interval_key) or (5 if active else 180))
    last = str(match.get("lineups", {}).get("checked_at") or "").strip()
    if not last:
        return True
    try:
        if last.endswith("Z"):
            last = last[:-1] + "+00:00"
        last_dt = datetime.fromisoformat(last).astimezone(timezone.utc)
    except Exception:
        return True
    return now - last_dt >= timedelta(minutes=interval)


def refresh_lineups_for_match(match, scheduler_config, now=None, active=False):
    existing = match.get("lineups") if isinstance(match.get("lineups"), dict) else {}
    if existing.get("manual"):
        return False
    now = now or datetime.now(timezone.utc)
    if not should_refresh_lineups(match, scheduler_config, now, active=active):
        return False

    urls = source_urls_for_match(match)
    max_sources = int(scheduler_config.get("lineup_refresh_max_sources") or 4)
    timeout = int(scheduler_config.get("lineup_refresh_timeout_seconds") or 10)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    best = None
    for url in urls[:max_sources]:
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code != 200:
                continue
            candidate = extract_lineup_from_html(response.text)
        except Exception:
            continue
        if not candidate:
            continue
        candidate["source_url"] = url
        if candidate["status"] == "confirmed":
            best = candidate
            break
        if not best:
            best = candidate

    checked_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not best:
        if existing.get("checked_at") == checked_at:
            return False
        match["lineups"] = dict(existing, checked_at=checked_at)
        return True

    best["checked_at"] = checked_at
    best["updated_at"] = checked_at
    changed = (
        existing.get("status") != best.get("status")
        or existing.get("text") != best.get("text")
        or existing.get("source_url") != best.get("source_url")
    )
    if changed or existing.get("checked_at") != checked_at:
        match["lineups"] = best
        return True
    return False
