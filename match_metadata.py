#!/usr/bin/env python3
import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests

from lineup_manager import extract_lineup_from_html
from pipeline_storage import parse_time


DEFAULT_METADATA_DOMAINS = [
    "fifa.com",
    "www.fifa.com",
    "espn.com",
    "www.espn.com",
    "bbc.com",
    "www.bbc.com",
    "bbc.co.uk",
    "www.bbc.co.uk",
    "skysports.com",
    "www.skysports.com",
    "fotmob.com",
    "www.fotmob.com",
    "sofascore.com",
    "www.sofascore.com",
    "wikipedia.org",
    "en.wikipedia.org",
]

RESULT_FINAL_MARKERS = (
    "full-time",
    "full time",
    "fulltime",
    "final score",
    "final:",
    " ft ",
    "(ft)",
    "eventcompleted",
    "match ended",
    "report",
)

RESULT_LIVE_MARKERS = (
    "live",
    "half-time",
    "half time",
    "halftime",
    "first half",
    "second half",
    "eventinprogress",
)


def utc_now(now=None):
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc)


def iso_now(now=None):
    return utc_now(now).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_hash(value):
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def ascii_fold(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def key_text(value):
    folded = ascii_fold(value).lower()
    folded = folded.replace("&", " and ")
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def compact_text(value):
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", value or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text.replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", text).strip()


def source_domain(url):
    host = urlparse(str(url or "")).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def domain_allowed(url, domains):
    domain = source_domain(url)
    if not domain:
        return False
    allowed = [str(item).lower().lstrip(".") for item in (domains or DEFAULT_METADATA_DOMAINS)]
    return any(domain == item or domain.endswith("." + item) for item in allowed)


def normalize_url(url):
    value = str(url or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if not value.startswith(("http://", "https://")):
        return ""
    parsed = urlparse(value)
    if "google." in parsed.netloc and parsed.path == "/url":
        target = parse_qs(parsed.query).get("q", [""])[0]
        if target:
            value = unquote(target)
    return value.split("#", 1)[0].strip()


def metadata_urls_for_match(match):
    values = []
    for field in ("metadata_urls", "metadata_url", "fixture_source_url", "score_source_url", "lineup_source_url"):
        value = match.get(field)
        if isinstance(value, list):
            values.extend(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            values.append(value.strip())

    seen = set()
    result = []
    for value in values:
        url = normalize_url(value)
        key = url.rstrip("/")
        if key and key not in seen:
            seen.add(key)
            result.append(url)
    return result


def metadata_record(url, role="metadata"):
    return {
        "url": url,
        "domain": source_domain(url),
        "role": role,
        "discovered_at": iso_now(),
        "last_success_at": "",
        "fail_count": 0,
    }


def add_metadata_url(match, url, role="metadata"):
    url = normalize_url(url)
    if not url:
        return False
    urls = metadata_urls_for_match(match)
    if url.rstrip("/") in {item.rstrip("/") for item in urls}:
        return False
    urls.append(url)
    match["metadata_urls"] = urls
    records = match.get("metadata_records") if isinstance(match.get("metadata_records"), list) else []
    if not any(str(record.get("url") or "").rstrip("/") == url.rstrip("/") for record in records if isinstance(record, dict)):
        records.append(metadata_record(url, role=role))
        match["metadata_records"] = records
    return True


def mark_metadata_url(match, url, success):
    records = match.get("metadata_records") if isinstance(match.get("metadata_records"), list) else []
    url_key = normalize_url(url).rstrip("/")
    if not url_key:
        return False
    changed = False
    for record in records:
        if not isinstance(record, dict):
            continue
        if normalize_url(record.get("url")).rstrip("/") != url_key:
            continue
        if success:
            record["last_success_at"] = iso_now()
            record["fail_count"] = 0
        else:
            record["fail_count"] = int(record.get("fail_count") or 0) + 1
        changed = True
    if changed:
        match["metadata_records"] = records
    return changed


def should_refresh_metadata(match, scheduler_config, now=None, active=False, score_only=False):
    if scheduler_config.get("metadata_refresh_enabled") is False:
        return False
    now = utc_now(now)
    if score_only:
        interval = int(scheduler_config.get("score_refresh_interval_minutes") or 10)
    else:
        key = "metadata_active_refresh_interval_minutes" if active else "metadata_refresh_interval_minutes"
        interval = int(scheduler_config.get(key) or (10 if active else 60))
    last = str(match.get("metadata_checked_at") or "").strip()
    if not last:
        return True
    try:
        last_dt = parse_time(last)
    except Exception:
        return True
    return now - last_dt >= timedelta(minutes=interval)


def metadata_query(match):
    competition = match.get("competition") or match.get("league") or "FIFA World Cup 2026"
    return f"{match.get('team1') or ''} vs {match.get('team2') or ''} {competition} score lineups"


def search_checked_recently(match, scheduler_config, now):
    interval_hours = float(scheduler_config.get("metadata_search_interval_hours") or 6)
    last = str(match.get("metadata_search_checked_at") or "").strip()
    if not last:
        return False
    try:
        return utc_now(now) - parse_time(last) < timedelta(hours=interval_hours)
    except Exception:
        return False


def extract_urls_from_search_html(html):
    urls = []
    for raw in re.findall(r'href=["\']([^"\']+)["\']', html or "", flags=re.IGNORECASE):
        raw = unescape(raw)
        if raw.startswith("/url?"):
            query = raw.split("?", 1)[1]
            target = parse_qs(query).get("q", [""])[0]
            raw = unquote(target)
        elif raw.startswith("http"):
            raw = raw
        else:
            continue
        url = normalize_url(raw)
        if url:
            urls.append(url)
    return urls


def discover_metadata_urls_for_match(match, scheduler_config, now=None):
    if scheduler_config.get("metadata_search_enabled") is False:
        return []
    now = utc_now(now)
    if search_checked_recently(match, scheduler_config, now):
        return []

    domains = scheduler_config.get("metadata_trusted_domains") or DEFAULT_METADATA_DOMAINS
    provider = str(scheduler_config.get("metadata_search_provider") or "google").strip().lower()
    query = quote_plus(metadata_query(match))
    if provider == "duckduckgo":
        url = f"https://duckduckgo.com/html/?q={query}"
    else:
        url = f"https://www.google.com/search?q={query}"

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    timeout = int(scheduler_config.get("metadata_search_timeout_seconds") or 10)
    max_results = int(scheduler_config.get("metadata_search_max_results") or 4)
    found = []
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        for candidate in extract_urls_from_search_html(response.text):
            if domain_allowed(candidate, domains):
                found.append(candidate)
            if len(found) >= max_results:
                break
    except Exception:
        found = []
    match["metadata_search_checked_at"] = iso_now(now)
    return found


def collect_json_ld(value, results):
    if isinstance(value, list):
        for item in value:
            collect_json_ld(item, results)
    elif isinstance(value, dict):
        item_type = value.get("@type") or value.get("type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if any(str(item) == "SportsEvent" for item in types):
            results.append(value)
        for item in value.values():
            collect_json_ld(item, results)


def parse_structured_events(html):
    events = []
    for raw_script in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        payload = unescape(raw_script).strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except Exception:
            continue
        collect_json_ld(data, events)
    return events


def team_match_score(event, match):
    text = key_text(
        " ".join(
            str(value or "")
            for value in (
                event.get("name"),
                event.get("description"),
                event.get("url"),
            )
        )
    )
    team_keys = [key_text(match.get("team1")), key_text(match.get("team2"))]
    return sum(1 for key in team_keys if key and key in text)


def extract_status_from_text(text):
    lowered = " " + key_text(text).replace(" ", " ") + " "
    raw_lower = " " + str(text or "").lower() + " "
    if any(marker in raw_lower for marker in RESULT_FINAL_MARKERS):
        return "final"
    if any(marker in raw_lower for marker in RESULT_LIVE_MARKERS):
        return "live"
    if " eventcompleted " in lowered:
        return "final"
    if " eventinprogress " in lowered:
        return "live"
    return ""


def result_from_scores(match, score1, score2, status, source_url, checked_at):
    team1 = match.get("team1") or ""
    team2 = match.get("team2") or ""
    result = {
        "status": status or "live",
        "team1_score": int(score1),
        "team2_score": int(score2),
        "score_text": f"{team1} {int(score1)}-{int(score2)} {team2}",
        "source_url": source_url,
        "checked_at": checked_at,
    }
    if result["status"] == "final":
        result["final_at"] = checked_at
    return result


def find_score_near_teams(text, match):
    team1 = key_text(match.get("team1"))
    team2 = key_text(match.get("team2"))
    if not team1 or not team2:
        return None

    folded = ascii_fold(text).lower()
    folded = folded.replace("\u2013", "-").replace("\u2014", "-")
    folded = re.sub(r"[^a-z0-9:\-,]+", " ", folded)
    folded = re.sub(r"\s+", " ", folded).strip()

    t1_indices = [m.start() for m in re.finditer(re.escape(team1), folded)]
    t2_indices = [m.start() for m in re.finditer(re.escape(team2), folded)]
    if not t1_indices or not t2_indices:
        return None

    best_candidate = None
    min_dist = 999999

    for m in re.finditer(r"\b(\d{1,2})\s*[-:]\s*(\d{1,2})\b", folded):
        score_start, score_end = m.span()
        val1, val2 = int(m.group(1)), int(m.group(2))

        # Check if team1 and team2 are near this score (within 100 chars)
        t1_close = [idx for idx in t1_indices if abs(idx - score_start) <= 100]
        t2_close = [idx for idx in t2_indices if abs(idx - score_start) <= 100]

        if t1_close and t2_close:
            d1 = min(abs(idx - score_start) for idx in t1_close)
            d2 = min(abs(idx - score_start) for idx in t2_close)
            dist = d1 + d2

            # Filter out dates (e.g. 2026-06-22)
            pre_text = folded[max(0, score_start - 5):score_start]
            post_text = folded[score_end:min(len(folded), score_end + 5)]
            is_date = False
            if re.search(r"\b(19|20)\d{2}-?$", pre_text) or re.search(r"^[-?]?(19|20)\d{2}\b", post_text):
                is_date = True

            if not is_date and dist < min_dist:
                min_dist = dist
                closest_t1 = min(t1_close, key=lambda idx: abs(idx - score_start))
                closest_t2 = min(t2_close, key=lambda idx: abs(idx - score_start))

                snippet = folded[max(0, score_start - 120):score_end + 120]
                if closest_t1 < score_start < closest_t2:
                    best_candidate = (val1, val2, snippet)
                elif closest_t2 < score_start < closest_t1:
                    best_candidate = (val2, val1, snippet)
                else:
                    if closest_t1 < closest_t2:
                        best_candidate = (val1, val2, snippet)
                    else:
                        best_candidate = (val2, val1, snippet)

    if best_candidate:
        return best_candidate

    # Fallback to comma-separated format, e.g. "team1 1, team2 2"
    t1_esc = re.escape(team1)
    t2_esc = re.escape(team2)
    fallback_patterns = [
        rf"{t1_esc}\s+(\d{{1,2}})\s*,\s*{t2_esc}\s+(\d{{1,2}})",
        rf"{t2_esc}\s+(\d{{1,2}})\s*,\s*{t1_esc}\s+(\d{{1,2}})",
    ]
    for idx, pattern in enumerate(fallback_patterns):
        match_obj = re.search(pattern, folded)
        if match_obj:
            first, second = int(match_obj.group(1)), int(match_obj.group(2))
            snippet = folded[max(0, match_obj.start() - 120):match_obj.end() + 120]
            if idx == 0:
                return first, second, snippet
            return second, first, snippet

    return None


def extract_result_from_html(html, match, source_url="", checked_at=None):
    checked_at = checked_at or iso_now()
    events = parse_structured_events(html)
    best_event = None
    best_score = 0
    for event in events:
        score = team_match_score(event, match)
        if score > best_score:
            best_event = event
            best_score = score

    status_hint = ""
    if best_event:
        status_hint = extract_status_from_text(
            " ".join(str(best_event.get(key) or "") for key in ("eventStatus", "name", "description"))
        )

    text = compact_text(html)
    status = status_hint or extract_status_from_text(text)
    scores = find_score_near_teams(text, match)
    if not scores:
        return None
    score1, score2, score_snippet = scores
    snippet_status = extract_status_from_text(score_snippet)
    status = snippet_status or status_hint
    if not status:
        return None
    if "wikipedia.org" in source_domain(source_url) and status != "final":
        return None
    return result_from_scores(match, score1, score2, status, source_url, checked_at)


def merge_result(match, result):
    if not result:
        return False
    existing = match.get("result") if isinstance(match.get("result"), dict) else {}
    comparable = {k: result.get(k) for k in ("status", "team1_score", "team2_score", "score_text", "source_url")}
    existing_comparable = {k: existing.get(k) for k in comparable}
    if comparable == existing_comparable and existing.get("checked_at") == result.get("checked_at"):
        return False
    match["result"] = result
    if result.get("status") == "final":
        match["match_status"] = "completed"
    elif result.get("status") == "live":
        match["match_status"] = "live"
    return True


def merge_lineup(match, lineup, source_url, checked_at):
    if not lineup:
        return False
    existing = match.get("lineups") if isinstance(match.get("lineups"), dict) else {}
    next_lineup = dict(lineup)
    next_lineup["source_url"] = source_url
    next_lineup["checked_at"] = checked_at
    next_lineup["updated_at"] = checked_at
    changed = (
        existing.get("status") != next_lineup.get("status")
        or existing.get("text") != next_lineup.get("text")
        or existing.get("source_url") != next_lineup.get("source_url")
    )
    if changed or existing.get("checked_at") != checked_at:
        match["lineups"] = next_lineup
        return True
    return False


def refresh_match_metadata(match, scheduler_config, now=None, active=False, force=False, score_only=False):
    now = utc_now(now)
    if not force and not should_refresh_metadata(match, scheduler_config, now, active=active, score_only=score_only):
        return False

    changed = False
    domains = scheduler_config.get("metadata_trusted_domains") or DEFAULT_METADATA_DOMAINS
    for url in discover_metadata_urls_for_match(match, scheduler_config, now):
        if add_metadata_url(match, url, role="search"):
            changed = True

    urls = metadata_urls_for_match(match)
    existing_records = match.get("metadata_records") if isinstance(match.get("metadata_records"), list) else []
    existing_record_urls = {normalize_url(record.get("url")).rstrip("/") for record in existing_records if isinstance(record, dict)}
    for url in urls:
        if url.rstrip("/") not in existing_record_urls:
            existing_records.append(metadata_record(url, role="fixture"))
            existing_record_urls.add(url.rstrip("/"))
            changed = True
    if existing_records:
        match["metadata_records"] = existing_records

    max_sources = int(scheduler_config.get("metadata_refresh_max_sources") or 5)
    timeout = int(scheduler_config.get("metadata_refresh_timeout_seconds") or 12)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    checked_at = iso_now(now)

    for url in urls[:max_sources]:
        if not domain_allowed(url, domains):
            continue
        try:
            started = time.monotonic()
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            html = response.text
            mark_metadata_url(match, url, success=True)
        except Exception:
            if mark_metadata_url(match, url, success=False):
                changed = True
            continue

        result = extract_result_from_html(html, match, source_url=url, checked_at=checked_at)
        if merge_result(match, result):
            changed = True

        if not score_only:
            lineup = extract_lineup_from_html(html)
            if merge_lineup(match, lineup, url, checked_at):
                changed = True

        match["metadata_last_latency_ms"] = int((time.monotonic() - started) * 1000)
        if match.get("result", {}).get("status") == "final":
            break

    if match.get("metadata_checked_at") != checked_at:
        match["metadata_checked_at"] = checked_at
        changed = True
    return changed


def result_is_final(match):
    result = match.get("result") if isinstance(match.get("result"), dict) else {}
    return result.get("status") == "final"


def final_score_text(match):
    result = match.get("result") if isinstance(match.get("result"), dict) else {}
    if result.get("team1_score") is None or result.get("team2_score") is None:
        return ""
    return result.get("score_text") or (
        f"{match.get('team1') or ''} {result.get('team1_score')}-{result.get('team2_score')} {match.get('team2') or ''}"
    )


def completion_grace_expired(match, scheduler_config, now=None):
    now = utc_now(now)
    grace_hours = float(scheduler_config.get("completion_score_grace_hours") or 12)
    try:
        match_time = parse_time(match.get("match_time"))
    except Exception:
        return True
    active_end = float(scheduler_config.get("active_window_end_hours") or 3)
    deadline = match_time + timedelta(hours=active_end + grace_hours)
    return now >= deadline
