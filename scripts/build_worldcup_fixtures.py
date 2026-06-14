#!/usr/bin/env python3
import argparse
import json
import os
import re
from html import unescape

import requests

from fixture_manager import normalize_fixture_list
from pipeline_storage import write_json


def load_input(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read(), f"file:{path}"


def fetch_url(url):
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; stream-fixture-builder/1.0)"},
        timeout=20,
    )
    response.raise_for_status()
    return response.text, url


def coerce_json_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("fixtures", "matches", "events", "data", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def parse_json_payload(text):
    data = json.loads(text)
    fixtures = coerce_json_list(data)
    if not fixtures:
        raise ValueError("JSON payload does not contain a fixture list")
    return fixtures


def collect_sports_events(value, results):
    if isinstance(value, list):
        for item in value:
            collect_sports_events(item, results)
    elif isinstance(value, dict):
        event_type = value.get("@type") or value.get("type")
        if isinstance(event_type, list):
            is_sports_event = "SportsEvent" in event_type
        else:
            is_sports_event = str(event_type) == "SportsEvent"
        if is_sports_event:
            results.append(value)
        for item in value.values():
            collect_sports_events(item, results)


def parse_html_structured_data(text):
    events = []
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for raw_script in scripts:
        payload = unescape(raw_script).strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except Exception:
            continue
        collect_sports_events(data, events)

    fixtures = []
    for event in events:
        competitors = event.get("competitor") or event.get("performer") or []
        teams = []
        if isinstance(competitors, list):
            for competitor in competitors:
                if isinstance(competitor, dict):
                    name = competitor.get("name")
                else:
                    name = str(competitor)
                if name:
                    teams.append(name)
        fixture = {
            "match_name": event.get("name") or "",
            "match_time": event.get("startDate") or event.get("doorTime") or "",
            "fixture_source_url": event.get("url") or "",
        }
        if len(teams) >= 2:
            fixture["team1"] = teams[0]
            fixture["team2"] = teams[1]
        location = event.get("location")
        if isinstance(location, dict):
            fixture["venue"] = location.get("name") or ""
        fixtures.append(fixture)
    return fixtures


def parse_fixture_text(text):
    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        return parse_json_payload(text)
    fixtures = parse_html_structured_data(text)
    if fixtures:
        return fixtures
    raise ValueError("Could not extract fixtures. Provide JSON or an HTML page with SportsEvent structured data.")


def main():
    parser = argparse.ArgumentParser(description="Build canonical World Cup fixture JSON.")
    parser.add_argument("--input", help="Local JSON/HTML source file.")
    parser.add_argument("--source-url", help="Online JSON/HTML source URL.")
    parser.add_argument("--output", default="data/fixtures/worldcup_2026.json")
    args = parser.parse_args()

    if bool(args.input) == bool(args.source_url):
        parser.error("Provide exactly one of --input or --source-url.")

    if args.input:
        text, source_name = load_input(args.input)
    else:
        text, source_name = fetch_url(args.source_url)

    raw_fixtures = parse_fixture_text(text)
    fixtures, errors = normalize_fixture_list(raw_fixtures, source_name=source_name)
    for idx, error in errors:
        print(f"[!] Skipped row {idx}: {error}")
    if not fixtures:
        raise SystemExit("No valid fixtures were generated.")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    write_json(args.output, {"fixtures": fixtures})
    print(f"[+] Wrote {len(fixtures)} canonical fixture(s) to {args.output}")


if __name__ == "__main__":
    main()
