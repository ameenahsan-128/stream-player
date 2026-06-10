#!/usr/bin/env python3
import os
import sys
import json
import requests

CONFIG_FILE = "blogger_config.json"
PLAYER_FILE = "player.html"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"[-] Config file '{CONFIG_FILE}' not found. Please create it first.", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def get_access_token(config):
    print("[*] Fetching new access token using OAuth 2.0 refresh token...")
    url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": config.get("client_id"),
        "client_secret": config.get("client_secret"),
        "refresh_token": config.get("refresh_token"),
        "grant_type": "refresh_token"
    }
    try:
        response = requests.post(url, data=payload, timeout=15)
        response.raise_for_status()
        res_data = response.json()
        access_token = res_data.get("access_token")
        if not access_token:
            print("[-] Error: 'access_token' not found in response.", file=sys.stderr)
            sys.exit(1)
        print("[+] Access token retrieved successfully.")
        return access_token
    except Exception as e:
        print(f"[-] Failed to refresh access token: {e}", file=sys.stderr)
        if response is not None:
            print(f"[-] Response: {response.text}", file=sys.stderr)
        sys.exit(1)

def update_blogger_post(config, access_token, html_content):
    blog_id = config.get("blog_id")
    post_id = config.get("post_id")
    title = config.get("post_title", "Argentina Live Stream")
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    # Try as a Post first
    print(f"[*] Attempting to update Blogger post (ID: {post_id})...")
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/{post_id}"
    payload = {
        "kind": "blogger#post",
        "id": post_id,
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    
    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        print("[+] SUCCESS: Blogger post updated successfully!")
        print(f"[+] Post URL: {response.json().get('url')}")
        return
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            print("[*] Post not found. Attempting to update as a Blogger Page instead...")
        else:
            raise e
    except Exception as e:
        raise e

    # Fallback to Page
    url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/pages/{post_id}"
    payload = {
        "kind": "blogger#page",
        "id": post_id,
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content
    }
    
    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        print("[+] SUCCESS: Blogger page updated successfully!")
        print(f"[+] Page URL: {response.json().get('url')}")
    except Exception as e:
        print(f"[-] Failed to update Blogger post/page: {e}", file=sys.stderr)
        if response is not None:
            print(f"[-] Response: {response.text}", file=sys.stderr)
        sys.exit(1)

def main():
    config = load_config()
    
    if not os.path.exists(PLAYER_FILE):
        print(f"[-] Player file '{PLAYER_FILE}' not found. Please run the generator script first.", file=sys.stderr)
        sys.exit(1)
        
    with open(PLAYER_FILE, "r", encoding="utf-8") as f:
        player_html = f.read()
        
    access_token = get_access_token(config)
    update_blogger_post(config, access_token, player_html)

if __name__ == "__main__":
    main()
