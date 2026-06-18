#!/usr/bin/env python3
"""Re-authenticate the player blog OAuth credentials and obtain a new refresh token."""

import http.server
import json
import threading
import urllib.parse
import webbrowser

import requests

# Player blog credentials from master_config.json
with open("master_config.json", "r") as f:
    config = json.load(f)

CLIENT_ID = config["player_blog"]["client_id"]
CLIENT_SECRET = config["player_blog"]["client_secret"]
SCOPE = "https://www.googleapis.com/auth/blogger"
REDIRECT_URI = "http://127.0.0.1:8085"

auth_code_result = {"code": None}


class OAuthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            auth_code_result["code"] = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Authorization successful!</h2><p>You can close this tab and return to the terminal.</p></body></html>")
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            error = params.get("error", ["unknown"])[0]
            self.wfile.write(f"<html><body><h2>Authorization failed: {error}</h2></body></html>".encode())

    def log_message(self, format, *args):
        pass  # Suppress request logging


def main():
    # Build authorization URL
    auth_params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",  # Force consent to get a new refresh_token
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(auth_params)

    # Start local server to capture the redirect
    server = http.server.HTTPServer(("127.0.0.1", 8085), OAuthHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print("\n" + "=" * 60)
    print("PLAYER BLOG OAUTH RE-AUTHENTICATION")
    print("=" * 60)
    print(f"\nOpen this URL in your browser:\n")
    print(auth_url)
    print("\nWaiting for authorization...")

    # Wait for the callback
    server_thread.join(timeout=300)
    server.server_close()

    if not auth_code_result["code"]:
        print("\n[-] No authorization code received. Timed out or failed.")
        return

    print("\n[+] Authorization code received! Exchanging for tokens...")

    # Exchange auth code for tokens
    token_response = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": auth_code_result["code"],
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }, timeout=15)

    if token_response.status_code != 200:
        print(f"\n[-] Token exchange failed: {token_response.status_code}")
        print(token_response.text)
        return

    tokens = token_response.json()
    new_refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")

    if not new_refresh_token:
        print("\n[-] No refresh_token in response. Try again with prompt=consent.")
        print(json.dumps(tokens, indent=2))
        return

    print(f"\n[+] New refresh token obtained!")
    print(f"    Access token: {access_token[:20]}...")
    print(f"    Refresh token: {new_refresh_token[:30]}...")

    # Verify the token works with Blogger API
    print("\n[*] Verifying token with Blogger API...")
    blog_id = config["player_blog"]["blog_id"]
    verify = requests.get(
        f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if verify.status_code == 200:
        blog_info = verify.json()
        print(f"[+] Token verified! Blog: {blog_info.get('name', 'unknown')} ({blog_info.get('url', '')})")
    else:
        print(f"[!] Verification returned {verify.status_code}: {verify.text[:200]}")

    # Update master_config.json
    config["player_blog"]["refresh_token"] = new_refresh_token
    with open("master_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\n[+] master_config.json updated with new refresh token.")
    print("=" * 60)


if __name__ == "__main__":
    main()
