#!/usr/bin/env python3
import argparse
import copy
import json
import os

from automation_config import MASTER_CONFIG_FILE, load_automation_config


def read_text_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def ensure_sections(config):
    config.setdefault("player_blog", {})
    config.setdefault("portal_blog", {})
    config.setdefault("shared_social", {})
    config.setdefault("shared_smartlink", {})
    config.setdefault("scheduler", {})
    config["player_blog"].setdefault("player_slots", [])
    config["player_blog"].setdefault("ads", {})
    config["portal_blog"].setdefault("ads", {})
    config["shared_social"].setdefault("whatsapp_groups", [])
    config["shared_social"].setdefault("telegram_channels", [])
    config["shared_social"].setdefault("popup_delay_ms", 5000)
    config["shared_social"].setdefault("click_target", "_blank")
    config["shared_smartlink"].setdefault("enabled", True)
    config["scheduler"].setdefault("discovery_portals", [])
    config["scheduler"].setdefault("auto_discover_portals", [])
    config["scheduler"].setdefault("trusted_source_domains", [])
    return config


def append_unique(values, new_values):
    for value in new_values or []:
        value = value.strip()
        if value and value not in values:
            values.append(value)


def set_if_value(target, key, value):
    if value is not None:
        target[key] = value


def redact(value):
    value = str(value or "")
    if len(value) <= 8:
        return "***" if value else ""
    return value[:4] + "..." + value[-4:]


def redacted_config(config):
    result = copy.deepcopy(config)
    for section in ("player_blog", "portal_blog"):
        for key in ("client_secret", "refresh_token"):
            if result.get(section, {}).get(key):
                result[section][key] = redact(result[section][key])
    return result


def main():
    parser = argparse.ArgumentParser(description="Update master_config.json for the dual Blogger automation.")
    parser.add_argument("--config", default=MASTER_CONFIG_FILE, help="Path to master config JSON.")
    parser.add_argument("--show", action="store_true", help="Print the current config with secrets redacted.")

    parser.add_argument("--set-portal-blog-id")
    parser.add_argument("--set-portal-client-id")
    parser.add_argument("--set-portal-client-secret")
    parser.add_argument("--set-portal-refresh-token")
    parser.add_argument("--set-widget-base-url")
    parser.add_argument("--set-image-base-url")
    parser.add_argument("--set-default-channel-info")
    parser.add_argument("--set-default-league")
    parser.add_argument("--set-default-quality")

    parser.add_argument("--set-player-blog-id")
    parser.add_argument("--set-player-client-id")
    parser.add_argument("--set-player-client-secret")
    parser.add_argument("--set-player-refresh-token")
    parser.add_argument("--set-player-post-title")
    parser.add_argument("--add-player-slot", nargs=4, metavar=("SLOT_ID", "POST_ID", "URL", "TITLE"), action="append")

    parser.add_argument("--add-whatsapp", action="append", default=[], help="Append a WhatsApp group invite URL.")
    parser.add_argument("--add-telegram", action="append", default=[], help="Append a Telegram channel/group URL.")
    parser.add_argument("--set-social-popup-delay-ms", type=int)
    parser.add_argument("--set-social-click-target", choices=["_blank", "_self"])
    parser.add_argument("--set-smartlink-url")
    parser.add_argument("--set-smartlink-text")
    parser.add_argument("--enable-smartlink", action="store_true")
    parser.add_argument("--disable-smartlink", action="store_true")
    parser.add_argument("--ad-top-file", help="Read top ad HTML/script from a file.")
    parser.add_argument("--ad-bottom-file", help="Read bottom/popup ad HTML/script from a file.")
    parser.add_argument("--ad-top-inline", help="Set top ad HTML/script directly.")
    parser.add_argument("--ad-bottom-inline", help="Set bottom/popup ad HTML/script directly.")
    parser.add_argument("--portal-ad-top-file", help="Read portal top 300x250 ad HTML/script from a file.")
    parser.add_argument("--portal-ad-popup-file", help="Read portal popup 300x250 ad HTML/script from a file.")
    parser.add_argument("--portal-ad-popunder-file", help="Read portal popunder script from a file.")
    parser.add_argument("--portal-ad-social-bar-file", help="Read portal social bar script from a file.")
    parser.add_argument("--portal-ad-top-inline")
    parser.add_argument("--portal-ad-popup-inline")
    parser.add_argument("--portal-ad-popunder-inline")
    parser.add_argument("--portal-ad-social-bar-inline")
    parser.add_argument("--player-ad-head-file", help="Read player blog head ad script from a file.")
    parser.add_argument("--player-ad-bottom-file", help="Read player blog bottom ad script from a file.")
    parser.add_argument("--player-ad-head-inline")
    parser.add_argument("--player-ad-bottom-inline")
    parser.add_argument("--add-discovery-portal", action="append", default=[], help="Append a scheduler discovery portal URL.")
    parser.add_argument("--add-trusted-source-domain", action="append", default=[], help="Append a trusted source host/domain.")
    parser.add_argument("--set-source-refresh-interval-seconds", type=int)
    parser.add_argument("--set-auto-discover-interval-seconds", type=int)
    parser.add_argument("--set-max-sources-per-match", type=int)
    parser.add_argument("--random-btn-text")
    parser.add_argument("--random-btn-url")

    args = parser.parse_args()
    config = ensure_sections(load_automation_config(args.config))
    portal = config["portal_blog"]
    player = config["player_blog"]
    shared_social = config["shared_social"]
    shared_smartlink = config["shared_smartlink"]
    scheduler = config["scheduler"]

    set_if_value(portal, "blog_id", args.set_portal_blog_id)
    set_if_value(portal, "client_id", args.set_portal_client_id)
    set_if_value(portal, "client_secret", args.set_portal_client_secret)
    set_if_value(portal, "refresh_token", args.set_portal_refresh_token)
    set_if_value(portal, "widget_base_url", args.set_widget_base_url)
    set_if_value(portal, "image_base_url", args.set_image_base_url)
    set_if_value(portal, "default_channel_info", args.set_default_channel_info)
    set_if_value(portal, "default_league", args.set_default_league)
    set_if_value(portal, "default_quality", args.set_default_quality)
    set_if_value(portal, "random_btn_text", args.random_btn_text)
    set_if_value(portal, "random_btn_url", args.random_btn_url)

    set_if_value(player, "blog_id", args.set_player_blog_id)
    set_if_value(player, "client_id", args.set_player_client_id)
    set_if_value(player, "client_secret", args.set_player_client_secret)
    set_if_value(player, "refresh_token", args.set_player_refresh_token)
    set_if_value(player, "post_title", args.set_player_post_title)

    if args.ad_top_file:
        portal["ads"]["top_300x250"] = read_text_file(args.ad_top_file)
    if args.ad_bottom_file:
        portal["ads"]["popup_300x250"] = read_text_file(args.ad_bottom_file)
    if args.ad_top_inline is not None:
        portal["ads"]["top_300x250"] = args.ad_top_inline
    if args.ad_bottom_inline is not None:
        portal["ads"]["popup_300x250"] = args.ad_bottom_inline

    if args.portal_ad_top_file:
        portal["ads"]["top_300x250"] = read_text_file(args.portal_ad_top_file)
    if args.portal_ad_popup_file:
        portal["ads"]["popup_300x250"] = read_text_file(args.portal_ad_popup_file)
    if args.portal_ad_popunder_file:
        portal["ads"]["popunder"] = read_text_file(args.portal_ad_popunder_file)
    if args.portal_ad_social_bar_file:
        portal["ads"]["social_bar"] = read_text_file(args.portal_ad_social_bar_file)
    set_if_value(portal["ads"], "top_300x250", args.portal_ad_top_inline)
    set_if_value(portal["ads"], "popup_300x250", args.portal_ad_popup_inline)
    set_if_value(portal["ads"], "popunder", args.portal_ad_popunder_inline)
    set_if_value(portal["ads"], "social_bar", args.portal_ad_social_bar_inline)

    if args.player_ad_head_file:
        player["ads"]["head_script"] = read_text_file(args.player_ad_head_file)
    if args.player_ad_bottom_file:
        player["ads"]["bottom_script"] = read_text_file(args.player_ad_bottom_file)
    set_if_value(player["ads"], "head_script", args.player_ad_head_inline)
    set_if_value(player["ads"], "bottom_script", args.player_ad_bottom_inline)

    append_unique(shared_social["whatsapp_groups"], args.add_whatsapp)
    append_unique(shared_social["telegram_channels"], args.add_telegram)
    set_if_value(shared_social, "popup_delay_ms", args.set_social_popup_delay_ms)
    set_if_value(shared_social, "click_target", args.set_social_click_target)

    set_if_value(shared_smartlink, "url", args.set_smartlink_url)
    set_if_value(shared_smartlink, "text", args.set_smartlink_text)
    if args.enable_smartlink:
        shared_smartlink["enabled"] = True
    if args.disable_smartlink:
        shared_smartlink["enabled"] = False

    append_unique(scheduler["discovery_portals"], args.add_discovery_portal)
    append_unique(scheduler["auto_discover_portals"], args.add_discovery_portal)
    append_unique(scheduler["trusted_source_domains"], args.add_trusted_source_domain)
    set_if_value(scheduler, "source_refresh_interval_seconds", args.set_source_refresh_interval_seconds)
    set_if_value(scheduler, "auto_discover_interval_seconds", args.set_auto_discover_interval_seconds)
    set_if_value(scheduler, "max_sources_per_match", args.set_max_sources_per_match)

    for slot in args.add_player_slot or []:
        slot_id, post_id, url, title = slot
        existing = next((item for item in player["player_slots"] if item.get("id") == slot_id), None)
        slot_data = {"id": slot_id, "post_id": post_id, "url": url, "title": title}
        if existing:
            existing.update(slot_data)
        else:
            player["player_slots"].append(slot_data)

    save_json(args.config, config)

    if args.show:
        print(json.dumps(redacted_config(config), indent=2))
    else:
        print(f"[+] Updated {os.path.abspath(args.config)}")


if __name__ == "__main__":
    main()
