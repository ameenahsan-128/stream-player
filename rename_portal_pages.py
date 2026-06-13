#!/usr/bin/env python3
import argparse
import re
import sys
from urllib.parse import urlparse

import requests

from automation_config import get_portal_blog_config, has_oauth, load_automation_config
from precreate_posts import get_access_token


SKIP_PAGE_WORDS = {
    "about", "contact", "privacy", "policy", "disclaimer", "terms", "dmca",
    "sitemap", "home", "index"
}

TRAILING_WORDS = {
    "live", "stream", "streaming", "links", "portal", "watch", "free",
    "online", "hd", "info", "preview", "score", "scores", "match"
}

NAME_ALIASES = {
    "qat": "Qatar",
    "qater": "Qatar",
    "switz": "Switzerland",
    "swi": "Switzerland",
    "scot": "Scotland",
    "sco": "Scotland",
    "aus": "Australia",
    "turk": "Turkey",
    "turkiye": "Turkey",
    "mor": "Morocco",
    "para": "Paraguay",
    "usa": "USA",
    "lsg": "LSG",
    "rr": "RR",
    "ipl": "IPL",
    "fc": "FC",
}


def list_blogger_pages(config, access_token):
    blog_id = config.get("blog_id")
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages"
    pages = []
    page_token = None

    while True:
        params = {"fetchBodies": "false", "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(url, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        pages.extend(data.get("items", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            return pages


def patch_page_title(config, access_token, page_id, title):
    blog_id = config.get("blog_id")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    response = requests.patch(url, headers=headers, json={"title": title}, timeout=20)
    response.raise_for_status()
    return response.json()


def slug_text_from_page(page):
    url = page.get("url") or ""
    path = urlparse(url).path
    stem = path.rsplit("/", 1)[-1]
    stem = re.sub(r"\.html?$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[-_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or page.get("title", "")


def cleanup_match_text(text):
    text = str(text or "").strip()
    text = re.sub(r"\.(?:html?|php|asp|jsp)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[-_/]+", " ", text)
    text = re.sub(r"\b(?:19|20)\d{2}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    while words and words[-1].lower() in TRAILING_WORDS:
        words.pop()
    return " ".join(words)


def title_case_team(value):
    words = []
    for raw in re.findall(r"[a-zA-Z0-9]+", value or ""):
        low = raw.lower()
        if low in NAME_ALIASES:
            words.append(NAME_ALIASES[low])
        elif raw.isupper() and len(raw) <= 4:
            words.append(raw)
        else:
            words.append(raw.capitalize())
    return " ".join(words).strip()


def desired_title_for_page(page):
    info_title = desired_info_title_for_page(page)
    if info_title:
        return info_title

    url_text = cleanup_match_text(slug_text_from_page(page))
    title_text = cleanup_match_text(page.get("title", ""))

    for candidate in (url_text, title_text):
        lower_words = set(candidate.lower().split())
        if lower_words & SKIP_PAGE_WORDS:
            continue
        match = re.search(r"(.+?)\bvs\b(.+)", candidate, flags=re.IGNORECASE)
        if not match:
            continue
        team1 = title_case_team(match.group(1))
        team2 = title_case_team(match.group(2))
        if not team1 or not team2:
            continue
        return f"{team1} vs {team2} Live Streaming Links"
    return None


def desired_info_title_for_page(page):
    path = urlparse(page.get("url") or "").path
    stem = path.rsplit("/", 1)[-1]
    stem = re.sub(r"\.html?$", "", stem, flags=re.IGNORECASE).strip().lower()

    if stem in SKIP_PAGE_WORDS or stem in ("about-us", "contact-us"):
        return None

    live_match = re.fullmatch(r"world-cup-live-(\d+)", stem)
    if live_match:
        return f"WORLD CUP LIVE - {live_match.group(1)}"

    info_slot_match = re.fullmatch(r"world-cup-info-(\d+)", stem)
    if info_slot_match:
        return f"WORLD CUP INFO - {info_slot_match.group(1)}"

    info_match = re.fullmatch(r"([a-z0-9]+)-info", stem)
    if info_match:
        return f"{info_match.group(1).upper()} INFO"

    return None


def is_streaming_match_page(page, desired_title):
    if not desired_title:
        return False
    current_or_url = f"{page.get('title', '')} {page.get('url', '')}".lower()
    if re.search(r"/p/(?:world-cup-(?:live|info)-\d+|[a-z0-9]+-info)\.html", current_or_url):
        return True
    combined = f"{page.get('title', '')} {page.get('url', '')}".lower()
    if any(word in combined for word in ("privacy", "contact", "disclaimer", "terms", "dmca", "about")):
        return False
    return " vs " in desired_title.lower() and any(word in combined for word in ("stream", "streaming", "live"))


def main():
    parser = argparse.ArgumentParser(description="Normalize goforsports Blogger Page titles without changing URLs.")
    parser.add_argument("--apply", action="store_true", help="Apply title-only Page renames. Default is dry-run.")
    parser.add_argument("--include-unchanged", action="store_true", help="Print pages that already match the convention.")
    args = parser.parse_args()

    config = get_portal_blog_config(load_automation_config())
    if not has_oauth(config):
        print("[-] Portal Blogger OAuth is missing.")
        sys.exit(1)

    access_token = get_access_token(config)
    pages = list_blogger_pages(config, access_token)
    print(f"[*] Fetched {len(pages)} Blogger Page(s) from portal blog {config.get('blog_id')}.")

    candidates = []
    unchanged = []
    skipped = []
    for page in pages:
        desired = desired_title_for_page(page)
        if not is_streaming_match_page(page, desired):
            skipped.append(page)
            continue
        current = page.get("title") or ""
        if current == desired:
            unchanged.append(page)
            continue
        candidates.append((page, desired))

    if args.include_unchanged and unchanged:
        print("\nAlready consistent:")
        for page in unchanged:
            print(f"- {page.get('title')} | {page.get('url')}")

    if not candidates:
        print("\n[+] No Page title renames needed.")
        return

    print("\nPage title renames:")
    for page, desired in candidates:
        print(f"- {page.get('title')} -> {desired}")
        print(f"  URL: {page.get('url')}")

    if not args.apply:
        print(f"\n[dry-run] {len(candidates)} Page title(s) would be renamed. URLs would not be changed.")
        return

    changed = 0
    for page, desired in candidates:
        before_url = page.get("url")
        result = patch_page_title(config, access_token, page.get("id"), desired)
        after_url = result.get("url")
        if after_url != before_url:
            print(f"[-] URL changed unexpectedly for page {page.get('id')}: {before_url} -> {after_url}")
            sys.exit(2)
        changed += 1
        print(f"[+] Renamed: {desired} | URL unchanged: {after_url}")

    print(f"\n[+] Renamed {changed} Page title(s). No URLs changed.")


if __name__ == "__main__":
    main()
