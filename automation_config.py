#!/usr/bin/env python3
import copy
import json
import os

from pipeline_storage import (
    DEFAULT_DATA_DIR,
    DEFAULT_DIAGNOSTICS_DIR,
    DEFAULT_HISTORY_DIR,
    DEFAULT_LINKS_DIR,
    DEFAULT_LOCK_FILE,
    DEFAULT_PLAYERS_DIR,
    DEFAULT_RUNTIME_STATE_FILE,
    DEFAULT_SCHEDULE_FILE,
    DEFAULT_THUMBNAILS_DIR,
)

MASTER_CONFIG_FILE = "master_config.json"
LEGACY_PLAYER_CONFIG_FILE = "blogger_config.json"
LEGACY_PORTAL_CONFIG_FILE = "new_blogger_config.json"

DEFAULT_DISCOVERY_PORTALS = [
    "https://www.rd9sports.online/?m=1",
    "https://www.epicsports.in/",
    "https://www.epicsports.in/?view=tomo",
    "https://www.epicsports.blog/",
    "https://www.footem.co.in/",
    "https://www.90live.in/",
    "https://90live.yallatvlive.com/",
    "https://notebookpot.com/",
    "https://sportstrack.me/",
    "https://soccervent.xyz/",
    "https://vivo.epicsportss.com/",
    "https://fifawcbycxf.pages.dev/",
    "https://cxfoot.pages.dev/",
    "https://football.scoopnonstop.com/",
    "https://sportscorner3697.blogspot.com/",
    "https://loosports.xtva.shop/",
    "https://rexdexsports.in/",
    "https://www.rd9sports.pro/",
    "https://worldcup.epicsports.mobi/",
    "https://worldcup.epicsports.co.in/",
    "https://epicsports.mobi/",
    # --- New portals added ---
    "https://totalsportek.pro/",
    "https://streamsgate.tv/",
    "https://www.hesgoal.tv/",
    "https://soccerstreams100.io/",
    "https://redditsoccerstreams.tv/",
    "https://weakstreams.com/",
    "https://www.livesoccertv.com/",
    "https://cricfree.io/",
    "https://www.streameast.to/",
    "https://sportsurge.net/",
]

DEFAULT_TRUSTED_SOURCE_DOMAINS = [
    "www.epicsports.in",
    "epicsports.in",
    "www.epicsports.blog",
    "epicsports.blog",
    "epicsports.mobi",
    "www.epicsports.mobi",
    "worldcup.epicsports.mobi",
    "worldcup.epicsports.co.in",
    "epicsports.netmirror.info",
    "live.epicsportss.com",
    "www.rd9sports.pro",
    "rd9sports.pro",
    "rd9.riddlearena.com",
    "90live.in",
    "www.90live.in",
    "90live.yallatvlive.com",
    "notebookpot.com",
    "sportstrack.me",
    "sportstrack.yallatvlive.com",
    "soccervent.xyz",
    "vivo.epicsportss.com",
    "fifawcbycxf.pages.dev",
    "cxfoot.pages.dev",
    "football.scoopnonstop.com",
    "sportscorner3697.blogspot.com",
    "loosports.xtva.shop",
    "ok.ru",
    "www.ok.ru",
    "rexdexsports.in",
    "www.rexdexsports.in",
    "www.footem.co.in",
    "footem.co.in",
    "es.footem.in",
    # --- New trusted domains ---
    "totalsportek.pro",
    "www.totalsportek.pro",
    "streamsgate.tv",
    "www.streamsgate.tv",
    "hesgoal.tv",
    "www.hesgoal.tv",
    "soccerstreams100.io",
    "redditsoccerstreams.tv",
    "weakstreams.com",
    "www.weakstreams.com",
    "www.livesoccertv.com",
    "livesoccertv.com",
    "cricfree.io",
    "www.cricfree.io",
    "www.streameast.to",
    "streameast.to",
    "sportsurge.net",
    "www.sportsurge.net",
]

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

DEFAULT_SCHEDULER_CONFIG = {
    "data_dir": DEFAULT_DATA_DIR,
    "schedule_file": DEFAULT_SCHEDULE_FILE,
    "history_dir": DEFAULT_HISTORY_DIR,
    "runtime_state_file": DEFAULT_RUNTIME_STATE_FILE,
    "players_dir": DEFAULT_PLAYERS_DIR,
    "links_dir": DEFAULT_LINKS_DIR,
    "diagnostics_dir": DEFAULT_DIAGNOSTICS_DIR,
    "thumbnails_dir": DEFAULT_THUMBNAILS_DIR,
    "lock_file": DEFAULT_LOCK_FILE,
    "active_window_start_minutes": 15,
    "active_window_end_hours": 2.25,
    "pre_kickoff_cooldown_minutes": 1,
    "post_kickoff_fast_window_minutes": 20,
    "post_kickoff_fast_cooldown_minutes": 2,
    "post_kickoff_cooldown_minutes": 3,
    "loop_interval_seconds": 60,
    "fixture_sync_interval_seconds": 3600,
    "fixture_first_only": True,
    "auto_create_matches_from_discovery": False,
    "auto_discover_interval_seconds": 1800,
    "source_refresh_interval_seconds": 1800,
    "portal_precreate_hours_before": 72,
    "source_discovery_hours_before": 24,
    "lineup_refresh_enabled": True,
    "lineup_refresh_interval_minutes": 180,
    "lineup_active_refresh_interval_minutes": 5,
    "lineup_refresh_max_sources": 4,
    "lineup_refresh_timeout_seconds": 10,
    "metadata_refresh_enabled": True,
    "metadata_refresh_interval_minutes": 60,
    "metadata_active_refresh_interval_minutes": 10,
    "metadata_refresh_max_sources": 5,
    "metadata_refresh_timeout_seconds": 12,
    "metadata_search_enabled": True,
    "metadata_search_provider": "google",
    "metadata_search_interval_hours": 6,
    "metadata_search_max_results": 4,
    "metadata_search_timeout_seconds": 10,
    "score_refresh_interval_minutes": 10,
    "completion_score_grace_hours": 12,
    "metadata_trusted_domains": DEFAULT_METADATA_DOMAINS,
    "portal_refresh_on_metadata_changes": True,
    "source_thumbnail_enabled": False,
    "source_thumbnail_max_sources": 3,
    "source_thumbnail_timeout_seconds": 10,
    "blocked_thumbnail_source_domains": [
        "rd9sports.online",
        "rd9sports.pro",
        "rd9.riddlearena.com"
    ],
    "max_sources_per_match": 12,
    "auto_discover_portals": DEFAULT_DISCOVERY_PORTALS,
    "discovery_portals": DEFAULT_DISCOVERY_PORTALS,
    "trusted_source_domains": DEFAULT_TRUSTED_SOURCE_DOMAINS
}

DEFAULT_FIXTURE_API_CONFIG = {
    "enabled": True,
    "mode": "world_cup",
    "provider": "local_file",
    "fixture_file": "data/fixtures/worldcup_2026.json",
    "url": "",
    "allow_http_api": False,
    "mock_fallback": "",
    "allow_mock_fallback": False,
    "prune_unsynced": True,
    "merge_tolerance_hours": 18
}

DEFAULT_SHARED_SOCIAL_CONFIG = {
    "whatsapp_groups": [
        "https://chat.whatsapp.com/Kn4jkOys2oaJtuYbJv3Jlo",
        "https://chat.whatsapp.com/Dmf2muWHbeH8OWChLJwlTE",
        "https://chat.whatsapp.com/BY611PwCNMB99BWpgy0uCW",
        "https://whatsapp.com/channel/0029Vb7wU691nozD3ci8Bn2F",
    ],
    "telegram_channels": [
        "https://t.me/+43_hofAviBs4YWJl",
        "https://t.me/+pbS2o7aYwe04N2Nl",
        "https://t.me/+BRroGwpg2wJiZGZl",
        "https://t.me/+ItWztH1Rb6MxNDU1",
        "https://t.me/+3Xrk9OJsuT44YjQ1",
    ],
    "popup_delay_ms": 5000,
    "click_target": "_blank"
}

DEFAULT_SHARED_SMARTLINK_CONFIG = {
    "enabled": True,
    "text": "Continue To Live Coverage",
    "url": "https://throughalivemedication.com/xsz99637k8?key=50b1239907251e7fd3f28875c685fd32"
}

DEFAULT_PORTAL_ADS = {
    "top_300x250": "<script>atOptions = {'key':'26752c18ca8361bba098d31342583042','format':'iframe','height':250,'width':300,'params':{}};</script><script async src=\"https://throughalivemedication.com/26752c18ca8361bba098d31342583042/invoke.js\"></script>",
    "popup_300x250": "<script>atOptions = {'key':'26752c18ca8361bba098d31342583042','format':'iframe','height':250,'width':300,'params':{}};</script><script async src=\"https://throughalivemedication.com/26752c18ca8361bba098d31342583042/invoke.js\"></script>",
    "popunder": '<script async src="https://throughalivemedication.com/45/96/a9/4596a9a27ac7c137dd494fd1f200edbb.js"></script>',
    "social_bar": '<script async src="https://throughalivemedication.com/66/f1/17/66f11775fe2744312299821ac71b38f1.js"></script>'
}

DEFAULT_PLAYER_ADS = {
    "head_script": '<script async src="https://throughalivemedication.com/78/95/36/78953660b707ff1c75b91b933c958645.js"></script>',
    "bottom_script": '<script async src="https://throughalivemedication.com/78/95/36/78953660b707ff1c75b91b933c958645.js"></script>'
}


def read_json(filepath):
    if not os.path.exists(filepath):
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def is_configured(value):
    return bool(value) and not str(value).startswith("YOUR_")


def merge_missing(base, fallback):
    merged = copy.deepcopy(base or {})
    for key, value in (fallback or {}).items():
        if isinstance(value, dict):
            merged[key] = merge_missing(merged.get(key, {}), value)
        elif key not in merged or merged[key] in ("", None, [], {}):
            merged[key] = value
    return merged


def unique_values(values):
    result = []
    seen = set()
    for value in values or []:
        value = str(value).strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def first_list(*lists):
    for values in lists:
        normalized = unique_values(values)
        if normalized:
            return normalized
    return []


def merge_unique_lists(*lists):
    merged = []
    seen = set()
    for values in lists:
        for value in values or []:
            value = str(value).strip()
            key = value.lower()
            if value and key not in seen:
                merged.append(value)
                seen.add(key)
    return merged


def legacy_player_blog_config():
    legacy = read_json(LEGACY_PLAYER_CONFIG_FILE)
    slots = legacy.get("player_slots") or []
    if not slots and legacy.get("post_id"):
        slots = [{
            "id": "slot-1",
            "post_id": legacy.get("post_id"),
            "title": legacy.get("post_title") or "Live Stream Player",
            "url": legacy.get("master_player_url") or legacy.get("player_url") or ""
        }]

    return {
        "blog_id": legacy.get("blog_id", "4927968984731236030"),
        "client_id": legacy.get("client_id", ""),
        "client_secret": legacy.get("client_secret", ""),
        "refresh_token": legacy.get("refresh_token", ""),
        "post_id": legacy.get("post_id", ""),
        "post_title": legacy.get("post_title", "Live Stream Player"),
        "player_slots": slots,
        "create_dedicated_player_posts": legacy.get("create_dedicated_player_posts", True),
        "ads": {},
        "auto_discover_portals": legacy.get("auto_discover_portals", DEFAULT_DISCOVERY_PORTALS)
    }


def legacy_portal_blog_config():
    legacy = read_json(LEGACY_PORTAL_CONFIG_FILE)
    return {
        "blog_id": legacy.get("blog_id", ""),
        "client_id": legacy.get("client_id", ""),
        "client_secret": legacy.get("client_secret", ""),
        "refresh_token": legacy.get("refresh_token", ""),
        "widget_base_url": legacy.get("widget_base_url", ""),
        "match_info_widget_html": legacy.get("match_info_widget_html", ""),
        "match_info_widget_iframe_url": legacy.get("match_info_widget_iframe_url", ""),
        "match_info_widget_height": legacy.get("match_info_widget_height", 520),
        "fixture_widget_iframe_url": legacy.get("fixture_widget_iframe_url", ""),
        "fixture_widget_height": legacy.get("fixture_widget_height", 360),
        "default_match_info_iframe_enabled": legacy.get("default_match_info_iframe_enabled", False),
        "use_iframe_match_info_widget": legacy.get("use_iframe_match_info_widget", False),
        "default_match_info_iframe_domains": legacy.get("default_match_info_iframe_domains", [
            "90live.yallatvlive.com",
            "es.footem.in",
            "worldcup.epicsports.mobi",
            "worldcup.epicsports.co.in"
        ]),
        "world_cup_page_hero_image_url": legacy.get("world_cup_page_hero_image_url", ""),
        "prefer_existing_pages_on_refresh": legacy.get("prefer_existing_pages_on_refresh", True),
        "image_base_url": legacy.get("image_base_url", ""),
        "thumbnail_url_map": legacy.get("thumbnail_url_map", {}),
        "allow_global_thumbnail_fallback": legacy.get("allow_global_thumbnail_fallback", False),
        "master_player_url": legacy.get("master_player_url", ""),
        "whatsapp_groups": legacy.get("whatsapp_groups", []),
        "telegram_channels": legacy.get("telegram_channels", []),
        "ad_code_top": legacy.get("ad_code_top", ""),
        "ad_code_bottom": legacy.get("ad_code_bottom", ""),
        "random_btn_text": legacy.get("random_btn_text", ""),
        "random_btn_url": legacy.get("random_btn_url", ""),
        "ads": {},
        "default_match_genre": legacy.get("default_match_genre", legacy.get("default_league", "Live Sports")),
        "default_channel_info": legacy.get("default_channel_info", "Channel details will be updated before kickoff."),
        "bump_preview_published_on_refresh": legacy.get("bump_preview_published_on_refresh", True),
        "streaming_page_title_format": legacy.get("streaming_page_title_format", "{team_code} INFO"),
        "streaming_page_url_seed_format": legacy.get("streaming_page_url_seed_format", "{team1} vs {team2} Live Streaming"),
        "prominent_team_priority": legacy.get("prominent_team_priority", []),
        "team_title_codes": legacy.get("team_title_codes", {}),
        "embed_local_thumbnails_when_no_image_base_url": legacy.get("embed_local_thumbnails_when_no_image_base_url", True)
    }


def load_automation_config(master_path=MASTER_CONFIG_FILE):
    master = read_json(master_path)
    config = {
        "player_blog": legacy_player_blog_config(),
        "portal_blog": legacy_portal_blog_config(),
        "shared_social": copy.deepcopy(DEFAULT_SHARED_SOCIAL_CONFIG),
        "shared_smartlink": copy.deepcopy(DEFAULT_SHARED_SMARTLINK_CONFIG),
        "scheduler": copy.deepcopy(DEFAULT_SCHEDULER_CONFIG),
        "fixture_api": copy.deepcopy(DEFAULT_FIXTURE_API_CONFIG)
    }

    config["player_blog"] = merge_missing(master.get("player_blog", {}), config["player_blog"])
    config["portal_blog"] = merge_missing(master.get("portal_blog", {}), config["portal_blog"])
    config["shared_social"] = merge_missing(master.get("shared_social", {}), config["shared_social"])
    config["shared_smartlink"] = merge_missing(master.get("shared_smartlink", {}), config["shared_smartlink"])
    config["scheduler"] = merge_missing(master.get("scheduler", {}), config["scheduler"])
    config["fixture_api"] = merge_missing(master.get("fixture_api", {}), config["fixture_api"])

    config["portal_blog"]["ads"] = merge_missing(config["portal_blog"].get("ads", {}), DEFAULT_PORTAL_ADS)
    config["player_blog"]["ads"] = merge_missing(config["player_blog"].get("ads", {}), DEFAULT_PLAYER_ADS)
    config["portal_blog"]["streaming_page_title_format"] = (
        config["portal_blog"].get("streaming_page_title_format") or "{team_code} INFO"
    )
    config["portal_blog"]["streaming_page_url_seed_format"] = (
        config["portal_blog"].get("streaming_page_url_seed_format") or "{team1} vs {team2} Live Streaming"
    )
    if "embed_local_thumbnails_when_no_image_base_url" not in config["portal_blog"]:
        config["portal_blog"]["embed_local_thumbnails_when_no_image_base_url"] = True
    config["portal_blog"]["prominent_team_priority"] = first_list(
        config["portal_blog"].get("prominent_team_priority"),
        config["portal_blog"].get("title_priority_teams"),
        config["portal_blog"].get("priority_teams")
    )

    config["shared_social"]["whatsapp_groups"] = first_list(
        config["shared_social"].get("whatsapp_groups"),
        config["portal_blog"].get("whatsapp_groups"),
        config["player_blog"].get("whatsapp_groups")
    )
    config["shared_social"]["telegram_channels"] = first_list(
        config["shared_social"].get("telegram_channels"),
        config["portal_blog"].get("telegram_channels"),
        config["player_blog"].get("telegram_channels")
    )
    config["shared_social"]["popup_delay_ms"] = int(config["shared_social"].get("popup_delay_ms") or 5000)
    config["shared_social"]["click_target"] = config["shared_social"].get("click_target") or "_blank"

    if not config["scheduler"].get("auto_discover_portals"):
        config["scheduler"]["auto_discover_portals"] = (
            config["player_blog"].get("auto_discover_portals") or DEFAULT_DISCOVERY_PORTALS
        )
    if not config["scheduler"].get("discovery_portals"):
        config["scheduler"]["discovery_portals"] = config["scheduler"].get("auto_discover_portals") or DEFAULT_DISCOVERY_PORTALS
    if not config["scheduler"].get("source_refresh_interval_seconds"):
        config["scheduler"]["source_refresh_interval_seconds"] = config["scheduler"].get("auto_discover_interval_seconds", 1800)
    if not config["scheduler"].get("trusted_source_domains"):
        config["scheduler"]["trusted_source_domains"] = DEFAULT_TRUSTED_SOURCE_DOMAINS
    if not config["scheduler"].get("metadata_trusted_domains"):
        config["scheduler"]["metadata_trusted_domains"] = DEFAULT_METADATA_DOMAINS

    config["scheduler"]["discovery_portals"] = merge_unique_lists(
        config["scheduler"].get("discovery_portals"),
        config["scheduler"].get("auto_discover_portals")
    )
    config["scheduler"]["auto_discover_portals"] = config["scheduler"]["discovery_portals"]
    config["scheduler"]["trusted_source_domains"] = merge_unique_lists(config["scheduler"].get("trusted_source_domains"))
    config["scheduler"]["metadata_trusted_domains"] = merge_unique_lists(config["scheduler"].get("metadata_trusted_domains"))

    if not config["player_blog"].get("player_slots") and config["player_blog"].get("post_id"):
        config["player_blog"]["player_slots"] = [{
            "id": "slot-1",
            "post_id": config["player_blog"].get("post_id"),
            "title": config["player_blog"].get("post_title") or "Live Stream Player",
            "url": config["player_blog"].get("master_player_url") or ""
        }]

    return config


def attach_shared_features(blog_config, root_config):
    config = copy.deepcopy(blog_config or {})
    shared_social = get_shared_social_config(root_config)
    shared_smartlink = get_shared_smartlink_config(root_config)

    config["whatsapp_groups"] = first_list(
        shared_social.get("whatsapp_groups"),
        config.get("whatsapp_groups")
    )
    config["telegram_channels"] = first_list(
        shared_social.get("telegram_channels"),
        config.get("telegram_channels")
    )
    config["social_popup_delay_ms"] = int(shared_social.get("popup_delay_ms") or 5000)
    config["social_click_target"] = shared_social.get("click_target") or "_blank"
    config["smartlink"] = copy.deepcopy(shared_smartlink)
    return config


def get_shared_social_config(config):
    return merge_missing((config or {}).get("shared_social", {}), DEFAULT_SHARED_SOCIAL_CONFIG)


def get_shared_smartlink_config(config):
    return merge_missing((config or {}).get("shared_smartlink", {}), DEFAULT_SHARED_SMARTLINK_CONFIG)


def get_player_blog_config(config):
    return attach_shared_features((config or {}).get("player_blog", {}), config or {})


def get_portal_blog_config(config):
    return attach_shared_features((config or {}).get("portal_blog", {}), config or {})


def get_scheduler_config(config):
    return copy.deepcopy((config or {}).get("scheduler", DEFAULT_SCHEDULER_CONFIG))


def get_fixture_api_config(config):
    return merge_missing((config or {}).get("fixture_api", {}), DEFAULT_FIXTURE_API_CONFIG)


def get_player_slots(config):
    player_blog = get_player_blog_config(config)
    slots = player_blog.get("player_slots") or []
    normalized = []
    for idx, slot in enumerate(slots, 1):
        post_id = str(slot.get("post_id", "")).strip()
        if not post_id:
            continue
        normalized.append({
            "id": str(slot.get("id") or f"slot-{idx}"),
            "post_id": post_id,
            "title": slot.get("title") or player_blog.get("post_title") or f"Live Stream Player {idx}",
            "url": slot.get("url") or ""
        })
    return normalized


def has_oauth(config):
    return all(is_configured(config.get(k)) for k in ["client_id", "client_secret", "refresh_token", "blog_id"])
