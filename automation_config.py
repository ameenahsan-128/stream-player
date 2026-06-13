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
    "https://www.footem.site/",
    "https://90live.in/",
    "https://www.rd9sports.pro/",
    "https://worldcup.epicsports.mobi/",
    "https://worldcup.epicsports.co.in/",
    "https://epicsports.mobi/",
    "http://footm.site/",
    "http://footem.site/",
    "https://www.90live.org/"
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
    "www.footem.site",
    "footem.site",
    "es.footem.in"
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
    "active_window_end_hours": 3,
    "pre_kickoff_cooldown_minutes": 1,
    "post_kickoff_cooldown_minutes": 10,
    "loop_interval_seconds": 60,
    "auto_discover_interval_seconds": 1800,
    "source_refresh_interval_seconds": 1800,
    "max_sources_per_match": 8,
    "auto_discover_portals": DEFAULT_DISCOVERY_PORTALS,
    "discovery_portals": DEFAULT_DISCOVERY_PORTALS,
    "trusted_source_domains": DEFAULT_TRUSTED_SOURCE_DOMAINS
}

DEFAULT_SHARED_SOCIAL_CONFIG = {
    "whatsapp_groups": [
        "https://chat.whatsapp.com/E9UG7hmlObr61yTP1CjvAk",
        "https://chat.whatsapp.com/KdNRt4WCQClLMmudKqe8Eg",
        "https://chat.whatsapp.com/BfN4WNiLpjUJFtJbUys0nk",
        "https://chat.whatsapp.com/Gm93HsMZDog9ExQ3KT9ED1",
        "https://chat.whatsapp.com/CxQ3yYdEqGu0dC5mpaFKZY",
        "https://chat.whatsapp.com/KmtHD4EPBe424XzohKqxSn",
        "https://chat.whatsapp.com/C54sNsV9o3X8MMlYJLzCRC",
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
    "top_300x250": "<script>atOptions = {'key':'26752c18ca8361bba098d31342583042','format':'iframe','height':250,'width':300,'params':{}};</script><script src=\"https://throughalivemedication.com/26752c18ca8361bba098d31342583042/invoke.js\"></script>",
    "popup_300x250": "<script>atOptions = {'key':'26752c18ca8361bba098d31342583042','format':'iframe','height':250,'width':300,'params':{}};</script><script src=\"https://throughalivemedication.com/26752c18ca8361bba098d31342583042/invoke.js\"></script>",
    "popunder": '<script src="https://throughalivemedication.com/45/96/a9/4596a9a27ac7c137dd494fd1f200edbb.js"></script>',
    "social_bar": '<script src="https://throughalivemedication.com/66/f1/17/66f11775fe2744312299821ac71b38f1.js"></script>'
}

DEFAULT_PLAYER_ADS = {
    "head_script": '<script src="https://throughalivemedication.com/78/95/36/78953660b707ff1c75b91b933c958645.js"></script>',
    "bottom_script": '<script src="https://throughalivemedication.com/78/95/36/78953660b707ff1c75b91b933c958645.js"></script>'
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
        "image_base_url": legacy.get("image_base_url", ""),
        "master_player_url": legacy.get("master_player_url", ""),
        "whatsapp_groups": legacy.get("whatsapp_groups", []),
        "telegram_channels": legacy.get("telegram_channels", []),
        "ad_code_top": legacy.get("ad_code_top", ""),
        "ad_code_bottom": legacy.get("ad_code_bottom", ""),
        "random_btn_text": legacy.get("random_btn_text", ""),
        "random_btn_url": legacy.get("random_btn_url", ""),
        "ads": {},
        "default_channel_info": legacy.get("default_channel_info", "Channel details will be updated before kickoff.")
    }


def load_automation_config(master_path=MASTER_CONFIG_FILE):
    master = read_json(master_path)
    config = {
        "player_blog": legacy_player_blog_config(),
        "portal_blog": legacy_portal_blog_config(),
        "shared_social": copy.deepcopy(DEFAULT_SHARED_SOCIAL_CONFIG),
        "shared_smartlink": copy.deepcopy(DEFAULT_SHARED_SMARTLINK_CONFIG),
        "scheduler": copy.deepcopy(DEFAULT_SCHEDULER_CONFIG)
    }

    config["player_blog"] = merge_missing(master.get("player_blog", {}), config["player_blog"])
    config["portal_blog"] = merge_missing(master.get("portal_blog", {}), config["portal_blog"])
    config["shared_social"] = merge_missing(master.get("shared_social", {}), config["shared_social"])
    config["shared_smartlink"] = merge_missing(master.get("shared_smartlink", {}), config["shared_smartlink"])
    config["scheduler"] = merge_missing(master.get("scheduler", {}), config["scheduler"])

    config["portal_blog"]["ads"] = merge_missing(config["portal_blog"].get("ads", {}), DEFAULT_PORTAL_ADS)
    config["player_blog"]["ads"] = merge_missing(config["player_blog"].get("ads", {}), DEFAULT_PLAYER_ADS)

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

    config["scheduler"]["discovery_portals"] = merge_unique_lists(
        config["scheduler"].get("discovery_portals"),
        config["scheduler"].get("auto_discover_portals")
    )
    config["scheduler"]["auto_discover_portals"] = config["scheduler"]["discovery_portals"]
    config["scheduler"]["trusted_source_domains"] = merge_unique_lists(config["scheduler"].get("trusted_source_domains"))

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
