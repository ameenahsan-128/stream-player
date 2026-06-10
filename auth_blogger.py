#!/usr/bin/env python3
import os
import sys
import json
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

CONFIG_FILE = "blogger_config.json"
PORT = 8080
REDIRECT_URI = f"http://localhost:{PORT}"

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        parsed_url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed_url.query)
        
        if "code" in params:
            self.server.auth_code = params["code"][0]
            self.wfile.write(b"<html><body><h1>Authorization Successful!</h1><p>You can close this tab and return to the terminal.</p></body></html>")
        else:
            self.wfile.write(b"<html><body><h1>Authorization Failed</h1><p>No code parameter found in request.</p></body></html>")

def main():
    if not os.path.exists(CONFIG_FILE):
        print(f"[-] Config file '{CONFIG_FILE}' not found. Initializing generic config...")
        config = {
            "blog_id": "",
            "post_id": "",
            "client_id": "",
            "client_secret": "",
            "refresh_token": ""
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    else:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)

    # Prompt user for client_id and client_secret if not present
    client_id = config.get("client_id") or ""
    client_secret = config.get("client_secret") or ""
    blog_id = config.get("blog_id") or ""

    if not client_id or client_id.startswith("YOUR_"):
        client_id = input("[?] Enter your Google Client ID: ").strip()
    if not client_secret or client_secret.startswith("YOUR_"):
        client_secret = input("[?] Enter your Google Client Secret: ").strip()
    if not blog_id or blog_id.startswith("YOUR_"):
        blog_id = input("[?] Enter your Blogger Blog ID (can find in blogger dashboard URL): ").strip()

    # Save initial inputs
    config["client_id"] = client_id
    config["client_secret"] = client_secret
    config["blog_id"] = blog_id
    
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    # Build OAuth URL
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        "scope=https://www.googleapis.com/auth/blogger&"
        "access_type=offline&"
        "include_granted_scopes=true&"
        "response_type=code&"
        f"redirect_uri={urllib.parse.quote(REDIRECT_URI)}&"
        f"client_id={client_id}"
    )

    print("\n" + "="*70)
    print("GOOGLE OAUTH AUTHORIZATION")
    print("="*70)
    print("1. Opening authorization link in browser...")
    print(f"If it doesn't open automatically, open this URL manually:\n{auth_url}\n")
    
    server = HTTPServer(("localhost", PORT), OAuthCallbackHandler)
    server.auth_code = None
    
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("[*] Waiting for browser authentication response...")
    # Serve one request to get the code
    server.handle_request()
    
    if not server.auth_code:
        print("[-] Error: Did not receive authorization code.")
        sys.exit(1)
        
    print("[+] Received authorization code. Exchanging for tokens...")
    
    # Exchange code for tokens
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "code": server.auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    
    res = requests.post(token_url, data=token_data)
    if res.status_code != 200:
        print(f"[-] Token exchange failed: {res.text}")
        sys.exit(1)
        
    res_json = res.json()
    refresh_token = res_json.get("refresh_token")
    
    if not refresh_token:
        print("[-] Warning: No refresh token returned. If you have authorized this app before, you may need to revoke access in Google Account Settings and retry, or force prompt consent.")
        # Try to use access token or existing refresh token
        if "access_token" in res_json:
            print("[+] Access token received, but refresh token is missing.")
    else:
        config["refresh_token"] = refresh_token
        print("[+] Refresh token retrieved successfully!")
        
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        
    print(f"[+] Credentials saved to '{CONFIG_FILE}'!")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()
