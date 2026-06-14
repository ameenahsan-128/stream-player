#!/usr/bin/env python3
import copy
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

from pipeline_storage import match_key, parse_time

WORLD_CUP_COMPETITION = "FIFA World Cup 2026"
WORLD_CUP_SEASON = "2026"

TEAM_ALIASES = {
    "am turkey": "Australia",
    "aus": "Australia",
    "austrlia": "Australia",
    "austrliaturky": "Australia",
    "bosnia herzegovina": "Bosnia and Herzegovina",
    "bosnia": "Bosnia and Herzegovina",
    "bosniahrg": "Bosnia and Herzegovina",
    "cape verde": "Cabo Verde",
    "cabo verde": "Cabo Verde",
    "cote divoire": "Ivory Coast",
    "cote d ivoire": "Ivory Coast",
    "curacao": "Curacao",
    "cura": "Curacao",
    "czech republic": "Czechia",
    "czech": "Czechia",
    "korea republic": "South Korea",
    "south korea": "South Korea",
    "ir iran": "Iran",
    "iran": "Iran",
    "ivory coast": "Ivory Coast",
    "mor": "Morocco",
    "para": "Paraguay",
    "qat": "Qatar",
    "qater": "Qatar",
    "sco": "Scotland",
    "scot": "Scotland",
    "swi": "Switzerland",
    "swiss": "Switzerland",
    "switz": "Switzerland",
    "tur": "Turkey",
    "turk": "Turkey",
    "turkiye": "Turkey",
    "usa": "USA",
    "united states": "USA",
    "us": "USA",
}

TITLE_WORDS = {
    "and",
    "of",
    "the",
}

MERGE_LIST_FIELDS = (
    "source_url",
    "source_urls",
    "source_records",
)

PRESERVE_IF_PRESENT_FIELDS = (
    "blogger_post_id",
    "blogger_post_url",
    "player_slot_id",
    "player_slot_post_id",
    "player_slot_url",
    "new_blogger_page_id",
    "new_blogger_page_url",
    "new_blogger_post_id",
    "new_blogger_post_url",
    "new_blog_iframe_set",
    "new_blog_prepare_set",
    "new_blog_ended_set",
    "iframe_embed_code",
    "lineups",
    "metadata_urls",
    "metadata_records",
    "metadata_checked_at",
    "metadata_search_checked_at",
    "match_status",
    "result",
    "content_hashes",
    "scrape_fail_count",
    "last_run_time",
    "thumbnail_url",
    "blogger_thumbnail_url",
    "feed_thumbnail_url",
    "page_hero_image_url",
    "portal_mode",
    "portal_managed",
)

CANONICAL_FIELDS = (
    "fixture_id",
    "competition",
    "season",
    "stage",
    "group",
    "team1",
    "team2",
    "match_name",
    "match_time",
    "venue",
    "fixture_source",
    "fixture_source_url",
    "fixture_checked_at",
)


def ascii_fold(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def normalize_space(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def key_text(value):
    folded = ascii_fold(value).lower()
    folded = folded.replace("&", " and ")
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return normalize_space(folded)


def title_team(value):
    text = normalize_space(value)
    if not text:
        return ""
    key = key_text(text)
    if key in TEAM_ALIASES:
        return TEAM_ALIASES[key]
    words = []
    for word in key.split():
        if word in TITLE_WORDS:
            words.append(word)
        elif len(word) <= 3 and word.upper() in ("USA", "UAE", "PSG"):
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    return " ".join(words)


def team_key(value):
    titled = title_team(value)
    return key_text(titled)


def split_match_name(match_name):
    text = normalize_space(match_name)
    parts = re.split(r"\s+(?:vs|v)\s+", text, flags=re.IGNORECASE, maxsplit=1)
    if len(parts) == 2:
        return title_team(parts[0]), title_team(parts[1])
    return title_team(text), ""


def normalize_match_name(team1, team2):
    team1 = title_team(team1)
    team2 = title_team(team2)
    if team1 and team2:
        return f"{team1} Vs {team2}"
    return team1 or team2 or "TBA Vs TBA"


def iso_time(value):
    return parse_time(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def fixture_slug(*parts):
    text = "_".join(key_text(part).replace(" ", "_") for part in parts if part)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "fixture"


def fixture_id_from_parts(competition, season, match_time, team1, team2):
    time_slug = parse_time(match_time).strftime("%Y%m%d_%H%M")
    parts = [competition]
    if season and key_text(season) not in key_text(competition):
        parts.append(season)
    parts.extend([time_slug, team1, team2])
    return fixture_slug(*parts)


def source_domain(url):
    host = urlparse(str(url or "")).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def extract_teams(item):
    team1 = (
        item.get("team1")
        or item.get("home_team")
        or item.get("home")
        or item.get("team_a")
    )
    team2 = (
        item.get("team2")
        or item.get("away_team")
        or item.get("away")
        or item.get("team_b")
    )
    if not (team1 and team2):
        name = item.get("match_name") or item.get("name") or item.get("title") or ""
        team1, team2 = split_match_name(name)
    return title_team(team1), title_team(team2)


def normalize_fixture_item(item, source_name="fixture-file", checked_at=None):
    if not isinstance(item, dict):
        raise ValueError("fixture item must be an object")
    checked_at = checked_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    team1, team2 = extract_teams(item)
    match_time = (
        item.get("match_time")
        or item.get("kickoff")
        or item.get("start_time")
        or item.get("startDate")
        or item.get("time")
    )
    if not team1 or not team2:
        raise ValueError(f"fixture missing teams: {item}")
    if not match_time:
        raise ValueError(f"fixture missing match_time: {item}")

    match_time = iso_time(match_time)
    competition = (
        item.get("competition")
        or item.get("league")
        or item.get("tournament")
        or WORLD_CUP_COMPETITION
    )
    season = str(item.get("season") or WORLD_CUP_SEASON)
    stage = item.get("stage") or item.get("round") or "Group Stage"
    group = item.get("group") or item.get("pool") or ""
    fixture_id = str(item.get("fixture_id") or item.get("id") or "").strip()
    if not fixture_id:
        fixture_id = fixture_id_from_parts(competition, season, match_time, team1, team2)

    normalized = {
        "fixture_id": fixture_id,
        "competition": str(competition).strip(),
        "season": season,
        "stage": str(stage).strip(),
        "group": str(group).strip(),
        "team1": team1,
        "team2": team2,
        "match_name": normalize_match_name(team1, team2),
        "match_time": match_time,
        "venue": normalize_space(item.get("venue") or item.get("stadium") or item.get("ground") or ""),
        "fixture_source": item.get("fixture_source") or source_name,
        "fixture_source_url": item.get("fixture_source_url") or item.get("source_url") or "",
        "fixture_checked_at": checked_at,
    }
    return {key: value for key, value in normalized.items() if value not in ("", None)}


def normalize_fixture_list(items, source_name="fixture-file"):
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fixtures = []
    errors = []
    for idx, item in enumerate(items or [], 1):
        try:
            fixtures.append(normalize_fixture_item(item, source_name=source_name, checked_at=checked_at))
        except Exception as exc:
            errors.append((idx, str(exc)))
    return fixtures, errors


def is_world_cup_competition(value):
    key = key_text(value)
    return "world cup" in key and ("2026" in key or "fifa" in key)


def schedule_match_allowed(match, fixture_config=None):
    fixture_config = fixture_config or {}
    mode = str(fixture_config.get("mode") or fixture_config.get("fixture_mode") or "world_cup").lower()
    if mode not in ("world_cup", "worldcup", "fifa_world_cup"):
        return True
    return is_world_cup_competition(
        match.get("competition") or match.get("league") or match.get("tournament") or ""
    )


def team_pair_key(match):
    team1 = match.get("team1")
    team2 = match.get("team2")
    if not (team1 and team2):
        team1, team2 = split_match_name(match.get("match_name") or "")
    keys = sorted([team_key(team1), team_key(team2)])
    return tuple(key for key in keys if key)


def source_urls_for_match(match):
    values = []
    for field in ("source_url", "source_urls"):
        value = match.get(field)
        if isinstance(value, list):
            values.extend(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            values.append(value.strip())
    seen = set()
    result = []
    for url in values:
        key = url.rstrip("/")
        if key and key not in seen:
            seen.add(key)
            result.append(url)
    return result


def merge_sources(primary, secondary):
    urls = source_urls_for_match(primary) + source_urls_for_match(secondary)
    seen = set()
    merged_urls = []
    for url in urls:
        key = url.rstrip("/")
        if key and key not in seen:
            seen.add(key)
            merged_urls.append(url)
    if merged_urls:
        primary["source_url"] = merged_urls
    elif "source_url" not in primary:
        primary["source_url"] = []

    records = []
    for match in (primary, secondary):
        value = match.get("source_records")
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, dict))
    record_seen = set()
    merged_records = []
    for record in records:
        url = str(record.get("url") or "").strip()
        if not url or url in record_seen:
            continue
        record_seen.add(url)
        merged_records.append(record)
    if merged_records:
        primary["source_records"] = merged_records


def data_richness(match):
    score = 0
    for field in PRESERVE_IF_PRESENT_FIELDS:
        if match.get(field) not in (None, "", [], {}):
            score += 2
    score += len(source_urls_for_match(match))
    return score


def fixture_match_score(match, fixture, tolerance_hours=18):
    if match.get("fixture_id") and match.get("fixture_id") == fixture.get("fixture_id"):
        return 1000 + data_richness(match)
    if team_pair_key(match) != team_pair_key(fixture):
        return 0
    try:
        diff_hours = abs((parse_time(match.get("match_time")) - parse_time(fixture.get("match_time"))).total_seconds()) / 3600
    except Exception:
        diff_hours = tolerance_hours + 1
    if diff_hours <= tolerance_hours:
        return 800 - int(diff_hours * 10) + data_richness(match)
    return 0


def merge_existing_matches(matches):
    merged = copy.deepcopy(matches[0])
    for other in matches[1:]:
        for field in PRESERVE_IF_PRESENT_FIELDS:
            if merged.get(field) in (None, "", [], {}) and other.get(field) not in (None, "", [], {}):
                merged[field] = copy.deepcopy(other[field])
        merge_sources(merged, other)
    return merged


def apply_fixture(existing, fixture):
    merged = copy.deepcopy(existing or {})
    for field in CANONICAL_FIELDS:
        value = fixture.get(field)
        if value not in (None, ""):
            merged[field] = value
    merged["match_key"] = match_key(merged)
    merged.setdefault("source_url", source_urls_for_match(existing or {}))
    if not merged.get("status") or merged.get("status") == "out_of_scope":
        merged["status"] = "pending"
    return merged


def new_match_from_fixture(fixture):
    match = apply_fixture({"source_url": [], "blogger_post_id": "", "status": "pending"}, fixture)
    return match


def merge_schedule_with_fixtures(schedule, fixtures, prune_unsynced=True, tolerance_hours=18):
    schedule = list(schedule or [])
    fixtures = sorted(list(fixtures or []), key=lambda item: item.get("match_time") or "")
    used = set()
    next_schedule = []
    removed = []
    stats = {
        "added": 0,
        "updated": 0,
        "deduped": 0,
        "removed": 0,
    }

    for fixture in fixtures:
        scored = []
        for idx, match in enumerate(schedule):
            if idx in used:
                continue
            score = fixture_match_score(match, fixture, tolerance_hours=tolerance_hours)
            if score > 0:
                scored.append((score, idx, match))

        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            matched_indexes = [idx for _score, idx, _match in scored]
            matched_matches = [schedule[idx] for idx in matched_indexes]
            matched_matches.sort(key=data_richness, reverse=True)
            before = copy.deepcopy(matched_matches[0])
            merged_existing = merge_existing_matches(matched_matches)
            next_match = apply_fixture(merged_existing, fixture)
            if next_match != before:
                stats["updated"] += 1
            if len(matched_indexes) > 1:
                stats["deduped"] += len(matched_indexes) - 1
            used.update(matched_indexes)
            next_schedule.append(next_match)
        else:
            next_schedule.append(new_match_from_fixture(fixture))
            stats["added"] += 1

    for idx, match in enumerate(schedule):
        if idx in used:
            continue
        if prune_unsynced:
            removed_match = copy.deepcopy(match)
            removed_match["status"] = "out_of_scope"
            removed.append(removed_match)
            stats["removed"] += 1
        else:
            next_schedule.append(match)

    try:
        next_schedule.sort(key=lambda item: parse_time(item.get("match_time")))
    except Exception:
        pass
    return next_schedule, removed, stats


def source_record(url, score=0):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "url": url,
        "domain": source_domain(url),
        "score": score,
        "discovered_at": now,
        "last_success_at": "",
        "fail_count": 0,
    }
