#!/usr/bin/env python3
import base64
from io import BytesIO
import os
import re
from urllib.parse import urlparse

from PIL import Image
import requests

from pipeline_storage import storage_config
from portal_renderer import slugify_match_name

DEFAULT_BLOCKED_THUMBNAIL_SOURCE_DOMAINS = [
    "rd9sports.online",
    "rd9sports.pro",
    "rd9.riddlearena.com",
]

DEFAULT_BLOCKED_THUMBNAIL_MARKERS = [
    "rd9sports",
    "riddlearena",
]


def thumbnail_filename(match):
    return f"thumb_{slugify_match_name(match.get('match_name', 'match'))}.jpg"


def thumbnail_path(config, match):
    return os.path.join(storage_config(config).get("thumbnails_dir"), thumbnail_filename(match))


def hostname(value):
    try:
        return urlparse(str(value or "").strip()).netloc.lower()
    except Exception:
        return ""


def domain_matches(host, domain):
    host = (host or "").lower()
    domain = (domain or "").lower().lstrip(".")
    return bool(host and domain and (host == domain or host.endswith("." + domain)))


def configured_blocked_domains(config=None, portal_config=None, scheduler_config=None):
    scheduler = scheduler_config or (config or {}).get("scheduler", {})
    values = []
    for source in (
        scheduler.get("blocked_thumbnail_source_domains"),
        scheduler.get("blocked_thumbnail_domains"),
        (portal_config or {}).get("blocked_thumbnail_source_domains"),
        DEFAULT_BLOCKED_THUMBNAIL_SOURCE_DOMAINS,
    ):
        if isinstance(source, list):
            values.extend(source)
        elif isinstance(source, str) and source.strip():
            values.append(source)
    result = []
    seen = set()
    for value in values:
        value = str(value).strip().lower().lstrip(".")
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def is_blocked_domain_url(url, blocked_domains):
    host = hostname(url)
    return any(domain_matches(host, domain) for domain in blocked_domains)


def is_blocked_thumbnail(match, thumbnail_url, blocked_domains):
    lowered = str(thumbnail_url or "").lower()
    if any(marker in lowered for marker in DEFAULT_BLOCKED_THUMBNAIL_MARKERS):
        return True
    if is_blocked_domain_url(thumbnail_url, blocked_domains):
        return True
    source_url = str(match.get("thumbnail_source_url") or "").strip()
    if any(marker in source_url.lower() for marker in DEFAULT_BLOCKED_THUMBNAIL_MARKERS):
        return True
    return bool(source_url and is_blocked_domain_url(source_url, blocked_domains))


def usable_image_base_url(portal_config):
    image_base = str((portal_config or {}).get("image_base_url") or "").strip()
    if not image_base:
        return ""
    lowered = image_base.lower()
    if "yourdomain.com" in lowered or lowered.startswith("file:"):
        return ""
    return image_base.rstrip("/")


def fallback_public_thumbnail_url(portal_config, match=None, blocked_domains=None):
    if not (portal_config or {}).get("allow_global_thumbnail_fallback", False):
        return ""
    blocked_domains = blocked_domains or []
    for key in (
        "world_cup_public_thumbnail_url",
        "world_cup_thumbnail_url",
        "fallback_public_thumbnail_url",
        "default_thumbnail_url",
    ):
        url = str((portal_config or {}).get(key) or "").strip()
        if url.startswith("http") and not is_blocked_domain_url(url, blocked_domains):
            return url
    return ""


def explicit_public_thumbnail_url(match, portal_config=None, blocked_domains=None):
    blocked_domains = blocked_domains or []
    values = []
    for key in (
        "thumbnail_url",
        "blogger_thumbnail_url",
        "uploaded_thumbnail_url",
        "public_thumbnail_url",
    ):
        value = str((match or {}).get(key) or "").strip()
        if value:
            values.append(value)

    thumbnail_map = (portal_config or {}).get("thumbnail_url_map") or {}
    if isinstance(thumbnail_map, dict):
        match_keys = {
            str((match or {}).get("match_key") or "").strip().lower(),
            slugify_match_name((match or {}).get("match_name", "")).lower(),
            str((match or {}).get("match_name") or "").strip().lower(),
        }
        for key, value in thumbnail_map.items():
            if str(key).strip().lower() in match_keys:
                values.append(str(value or "").strip())

    for url in values:
        if url.startswith("http") and not is_blocked_thumbnail(match or {}, url, blocked_domains):
            return url
    return ""


def public_thumbnail_url(config, portal_config, match):
    blocked_domains = configured_blocked_domains(config, portal_config)
    explicit = explicit_public_thumbnail_url(match, portal_config, blocked_domains)
    if explicit:
        return explicit
    image_base = usable_image_base_url(portal_config)
    if image_base:
        return f"{image_base}/{thumbnail_filename(match)}"
    return fallback_public_thumbnail_url(portal_config, match, blocked_domains)


def inline_thumbnail_enabled(portal_config):
    return bool((portal_config or {}).get("embed_local_thumbnails_when_no_image_base_url", True))


def file_to_data_uri(path, max_width=720, quality=76):
    if not path or not os.path.exists(path):
        return ""
    with Image.open(path) as image:
        image = image.convert("RGB")
        if image.width > max_width:
            ratio = max_width / float(image.width)
            image = image.resize((max_width, int(image.height * ratio)), Image.Resampling.LANCZOS)
        buf = BytesIO()
        image.save(buf, "JPEG", quality=quality, optimize=True)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def thumbnail_src(config, portal_config, match):
    public_url = public_thumbnail_url(config, portal_config, match)
    image_base = usable_image_base_url(portal_config)
    if image_base and public_url:
        return public_url

    local_uri = ""
    if inline_thumbnail_enabled(portal_config):
        local_uri = file_to_data_uri(thumbnail_path(config, match))
    return local_uri or public_url


def with_thumbnail_src(config, portal_config, match):
    enriched = dict(match)
    public_url = public_thumbnail_url(config, portal_config, enriched)
    if public_url:
        enriched["feed_thumbnail_url"] = public_url
    src = thumbnail_src(config, portal_config, enriched)
    if src:
        enriched["thumbnail_url"] = src
    return enriched


def sanitize_thumbnail_fields(match, config=None, portal_config=None, scheduler_config=None):
    blocked_domains = configured_blocked_domains(config, portal_config, scheduler_config)
    url = str(match.get("thumbnail_url") or "").strip()
    if url and is_blocked_thumbnail(match, url, blocked_domains):
        match.pop("thumbnail_url", None)
        match.pop("thumbnail_source_url", None)
        return True
    return False


def source_urls_for_match(match):
    value = match.get("source_url", "")
    if isinstance(value, list):
        return [str(url).strip() for url in value if str(url).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def extract_public_thumbnail_url(html):
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'<img[^>]+src=["\']([^"\']+)["\']',
    ]
    found = []
    for pattern in patterns:
        found.extend(re.findall(pattern, html or "", flags=re.IGNORECASE))

    for url in found:
        url = url.strip()
        lower = url.lower()
        if not lower.startswith("http"):
            continue
        if any(skip in lower for skip in ("logo", "avatar", "favicon", "blank.gif", "whatsapp", "telegram")):
            continue
        return url
    return ""


def refresh_thumbnail_url_from_sources(match, scheduler_config):
    if scheduler_config.get("source_thumbnail_enabled") is False:
        return False
    sanitize_thumbnail_fields(match, scheduler_config=scheduler_config)
    existing = str(match.get("thumbnail_url") or "").strip()
    if existing.startswith("http"):
        return False

    max_sources = int(scheduler_config.get("source_thumbnail_max_sources") or 3)
    timeout = int(scheduler_config.get("source_thumbnail_timeout_seconds") or 10)
    blocked_domains = configured_blocked_domains(scheduler_config=scheduler_config)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    for url in source_urls_for_match(match)[:max_sources]:
        if is_blocked_domain_url(url, blocked_domains):
            continue
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code != 200:
                continue
            image_url = extract_public_thumbnail_url(response.text)
        except Exception:
            continue
        if image_url and not is_blocked_domain_url(image_url, blocked_domains):
            match["thumbnail_url"] = image_url
            match["thumbnail_source_url"] = url
            return True
    return False
