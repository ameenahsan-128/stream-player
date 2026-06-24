#!/usr/bin/env python3
"""Manual OAuth Re-authentication for Google Blogger (Headless/CLI Friendly)."""
import json
import urllib.parse
import requests

def main():
    print("=" * 60)
    print("GOOGLE OAUTH MANUAL RE-AUTHORIZATION")
    print("=" * 60)
    
    with open("master_config.json", "r") as f:
        master_config = json.load(f)
        
    choice = input("Which blog do you want to authorize? (player/portal): ").strip().lower()
    if choice not in ("player", "portal"):
        print("[-] Invalid choice. Enter 'player' or 'portal'.")
        return
        
    section = "player_blog" if choice == "player" else "portal_blog"
    config = master_config[section]
    
    client_id = config.get("client_id")
    client_secret = config.get("client_secret")
    blog_id = config.get("blog_id")
    
    redirect_uri = "http://localhost:8080"
    
    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/blogger",
        "access_type": "offline",
        "prompt": "consent"
    }
    
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(auth_params)
    
    print(f"\n1. Open this URL in your browser and authorize the application:\n\n{auth_url}\n")
    print("2. Approve the authorization request.")
    print("3. Your browser will try to redirect to http://localhost:8080/... and show a connection error. This is normal.")
    print("4. Copy the entire address bar URL (or the 'code' parameter value) and paste it below.")
    
    pasted_input = input("\nPaste the URL or code here: ").strip()
    
    # Extract code if URL is pasted
    code = pasted_input
    if "code=" in pasted_input:
        try:
            parsed = urllib.parse.urlparse(pasted_input)
            params = urllib.parse.parse_qs(parsed.query)
            code = params["code"][0]
        except Exception:
            pass
            
    print(f"\n[*] Exchanging code for tokens...")
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }
    
    res = requests.post(token_url, data=token_data)
    if res.status_code != 200:
        print(f"[-] Token exchange failed: {res.text}")
        return
        
    res_json = res.json()
    new_refresh_token = res_json.get("refresh_token")
    access_token = res_json.get("access_token")
    
    if not new_refresh_token:
        print("[-] No refresh token returned. Try forcing consent or revoking app permissions in Google settings.")
        print(json.dumps(res_json, indent=2))
        return
        
    print(f"[+] Successfully obtained refresh token: {new_refresh_token[:30]}...")
    
    # Verify Blogger API
    print("[*] Verifying token with Blogger API...")
    verify = requests.get(
        f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if verify.status_code == 200:
        blog_info = verify.json()
        print(f"[+] Token verified! Blog: {blog_info.get('name', 'unknown')} ({blog_info.get('url', '')})")
        
        # Save config
        master_config[section]["refresh_token"] = new_refresh_token
        with open("master_config.json", "w", encoding="utf-8") as f:
            json.dump(master_config, f, indent=2)
        print(f"[+] Updated master_config.json with new refresh token for {section}!")
    else:
        print(f"[!] Verification failed: {verify.status_code} - {verify.text}")

if __name__ == "__main__":
    main()
