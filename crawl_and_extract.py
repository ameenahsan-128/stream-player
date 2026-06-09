#!/usr/bin/env python3
import os
import re
import sys
import json
import argparse
import requests
from urllib.parse import urljoin, urlparse, parse_qs
from html.parser import HTMLParser

class EpicLinkParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.results = []
        self.current_tag = None
        self.current_attrs = {}
        self.current_text = []

    def handle_starttag(self, tag, attrs):
        if tag in ("a", "button", "iframe"):
            self.current_tag = tag
            self.current_attrs = dict(attrs)
            self.current_text = []

    def handle_data(self, data):
        if self.current_tag:
            self.current_text.append(data)

    def handle_endtag(self, tag):
        if tag == self.current_tag:
            text = "".join(self.current_text).strip()
            url = None
            if "href" in self.current_attrs:
                url = self.current_attrs["href"]
            elif "src" in self.current_attrs and tag == "iframe":
                url = self.current_attrs["src"]
            elif "onclick" in self.current_attrs:
                onclick_val = self.current_attrs["onclick"]
                match = re.search(r"'(https?://[^'\s]+)'|\"(https?://[^\"]+)\"", onclick_val)
                if match:
                    url = match.group(1) or match.group(2)
            
            if url:
                resolved_url = urljoin(self.base_url, url)
                self.results.append({
                    "text": text.replace("\n", " ").strip(),
                    "url": resolved_url,
                    "tag": tag
                })
            
            self.current_tag = None
            self.current_attrs = {}
            self.current_text = []

def extract_root_links(root_url, pattern="^link", headers=None):
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        response = requests.get(root_url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"[-] Error fetching root URL {root_url}: {e}", file=sys.stderr)
        return []

    parser = EpicLinkParser(root_url)
    parser.feed(response.text)
    
    re_pattern = re.compile(pattern, re.IGNORECASE)
    matched_links = []
    
    for item in parser.results:
        text = item["text"]
        link_url = item["url"]
        
        if re_pattern.search(text) and item["tag"] in ("a", "button"):
            matched_links.append({
                "label": text,
                "url": link_url
            })
            
    return matched_links

def analyze_page(url, html, visited):
    stream_info = {
        "url": url,
        "type": "unknown",
        "streams": [],
        "clear_keys": {},
        "player": "unknown",
        "nested_links": []
    }
    
    # 1. Look for m3u8 and mpd links in the html / scripts
    m3u8_links = re.findall(r"[\x27\"](https?://[^\x27\"]+\.m3u8[^\x27\"]*)[\x27\"]", html, re.IGNORECASE)
    mpd_links = re.findall(r"[\x27\"](https?://[^\x27\"]+\.mpd[^\x27\"]*)[\x27\"]", html, re.IGNORECASE)
    
    # 2. Look for Shaka ClearKeys
    keys_match = re.search(r"clearKeys\s*:\s*\{([^}]+)\}", html, re.DOTALL)
    if keys_match:
        pairs = re.findall(r"[\x27\"]([0-9a-fA-F]{32})[\x27\"]\s*:\s*[\x27\"]([0-9a-fA-F]{32})[\x27\"]", keys_match.group(1))
        if pairs:
            stream_info["clear_keys"] = dict(pairs)
            stream_info["player"] = "shaka"
            stream_info["type"] = "dash"
            
    # 3. Look for JWPlayer setup blocks
    jw_match = re.search(r"jwplayer\(.*?\)\.setup\(\{(.*?)\}\)", html, re.DOTALL | re.IGNORECASE)
    if jw_match:
        stream_info["player"] = "jwplayer"
        file_match = re.search(r"file\s*:\s*[\x27\"]([^\x27\"]+)[\x27\"]", jw_match.group(1))
        if file_match:
            jw_file = file_match.group(1)
            stream_info["streams"].append(jw_file)
            if ".m3u8" in jw_file.lower():
                stream_info["type"] = "hls"
            elif ".mpd" in jw_file.lower():
                stream_info["type"] = "dash"
                
    # 4. Filter and append detected direct m3u8/mpd streams
    for link in m3u8_links:
        if ".js" not in link.lower() and link not in stream_info["streams"]:
            stream_info["streams"].append(link)
            if stream_info["type"] == "unknown":
                stream_info["type"] = "hls"
                stream_info["player"] = "hls.js / video.js"

    for link in mpd_links:
        if ".js" not in link.lower() and link not in stream_info["streams"]:
            stream_info["streams"].append(link)
            if stream_info["type"] == "unknown":
                stream_info["type"] = "dash"
                if stream_info["player"] == "unknown":
                    stream_info["player"] = "shaka"

    # 5. Extract iframe links
    parser = EpicLinkParser(url)
    parser.feed(html)
    
    iframes = [item["url"] for item in parser.results if item["tag"] == "iframe"]
    for iframe_url in iframes:
        if iframe_url not in visited and iframe_url != url:
            stream_info["nested_links"].append({
                "type": "iframe",
                "url": iframe_url
            })
            
    # 6. Extract JS stream maps (e.g. const streams = { "gm6": "...", ... })
    map_match = re.search(r"(?:const|let|var)?\s*streams\s*=\s*\{([^}]+)\}", html, re.DOTALL)
    if map_match:
        pairs = re.findall(r"[\x27\"]([^\x27\"]+)[\x27\"]\s*:\s*[\x27\"]([^\x27\"]+)[\x27\"]", map_match.group(1))
        stream_map = dict(pairs)
        
        parsed_url = urlparse(url)
        q_params = parse_qs(parsed_url.query)
        id_val = q_params.get("id", [None])[0]
        
        if id_val and id_val in stream_map:
            target_url = stream_map[id_val]
            if target_url and target_url != "#" and target_url not in visited:
                stream_info["nested_links"].append({
                    "type": "js_map_redirect",
                    "url": target_url
                })
        else:
            for k, target_url in stream_map.items():
                if target_url and target_url != "#" and target_url not in visited:
                    stream_info["nested_links"].append({
                        "type": f"js_map_{k}",
                        "url": target_url
                    })
                    
    return stream_info

def crawl_url_recursive(url, depth=0, max_depth=2, visited=None, headers=None):
    if visited is None:
        visited = set()
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
    if url in visited:
        return None
    visited.add(url)
    
    if depth > max_depth:
        return None
        
    print(f"{'  ' * depth}[*] Crawling page: {url}")
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        print(f"{'  ' * depth}[-] Failed to fetch {url}: {e}", file=sys.stderr)
        return {
            "url": url,
            "error": str(e),
            "nested_results": []
        }
        
    info = analyze_page(url, html, visited)
    info["nested_results"] = []
    
    # Check if there is a direct JS map redirect matching current query parameter
    has_redirect = any(item["type"] == "js_map_redirect" for item in info["nested_links"])
    
    for nested in info["nested_links"]:
        should_follow = False
        if nested["type"] == "iframe":
            should_follow = True
        elif nested["type"] == "js_map_redirect":
            should_follow = True
        elif not has_redirect and nested["type"].startswith("js_map_"):
            # Only follow other paths if we don't have a direct matched ID
            should_follow = True
            
        if should_follow:
            nested_res = crawl_url_recursive(nested["url"], depth+1, max_depth, visited, headers)
            if nested_res:
                info["nested_results"].append(nested_res)
                
    return info

def extract_final_stream_details(tree):
    if not tree:
        return None
        
    streams = list(tree.get("streams", []))
    clear_keys = dict(tree.get("clear_keys", {}))
    player = tree.get("player", "unknown")
    stream_type = tree.get("type", "unknown")
    
    for nested in tree.get("nested_results", []):
        nested_details = extract_final_stream_details(nested)
        if nested_details:
            streams.extend(nested_details["streams"])
            clear_keys.update(nested_details["clear_keys"])
            if player == "unknown" and nested_details["player"] != "unknown":
                player = nested_details["player"]
            if stream_type == "unknown" and nested_details["type"] != "unknown":
                stream_type = nested_details["type"]
                
    streams = list(dict.fromkeys(streams))
    
    return {
        "streams": streams,
        "clear_keys": clear_keys,
        "player": player,
        "type": stream_type
    }

def make_hls_snippet(url):
    return f"""<!-- HLS Player HTML -->
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<video id="video" controls autoplay width="100%" height="auto"></video>
<script>
  var video = document.getElementById('video');
  var videoSrc = '{url}';
  if (Hls.isSupported()) {{
    var hls = new Hls();
    hls.loadSource(videoSrc);
    hls.attachMedia(video);
  }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
    video.src = videoSrc;
  }}
</script>"""

def make_dash_snippet(url, keys):
    keys_json = json.dumps(keys, indent=4)
    keys_str = keys_json.replace("\n", "\n      ")
    return f"""<!-- Shaka Player HTML for DASH Stream with ClearKeys -->
<script src="https://ajax.googleapis.com/ajax/libs/shaka-player/4.3.5/shaka-player.compiled.js"></script>
<link rel="stylesheet" href="https://ajax.googleapis.com/ajax/libs/shaka-player/4.3.5/controls.css">
<div data-shaka-player-container style="max-width:800px">
  <video data-shaka-player id="video" autoplay style="width:100%;height:100%"></video>
</div>
<script>
  async function initPlayer() {{
    const video = document.getElementById('video');
    const player = new shaka.Player(video);
    
    player.configure({{
      drm: {{
        clearKeys: {keys_str}
      }}
    }});
    
    try {{
      await player.load('{url}');
      console.log("Stream loaded successfully!");
    }} catch (e) {{
      console.error("Error loading stream:", e);
    }}
  }}
  document.addEventListener('shaka-ui-loaded-failed', () => console.error("UI load failed"));
  document.addEventListener('shaka-ui-loaded', initPlayer);
</script>"""

def process_root_url(root_url, max_depth=2):
    print(f"\n[*] STEP 1: Scraping root page for links: {root_url}")
    matched_links = extract_root_links(root_url)
    print(f"[+] Found {len(matched_links)} stream buttons/links starting with 'Link'.")
    
    results = []
    
    for idx, item in enumerate(matched_links, 1):
        print(f"\n[*] STEP 2: Crawling button {idx}/{len(matched_links)}: {item['label']}")
        print(f"[*] Target URL: {item['url']}")
        
        visited = set()
        tree = crawl_url_recursive(item["url"], depth=0, max_depth=max_depth, visited=visited)
        details = extract_final_stream_details(tree)
        
        results.append({
            "label": item["label"],
            "root_target_url": item["url"],
            "details": details,
            "crawl_tree": tree
        })
        
    return results

def save_text_report(results, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("STREAMING TECHNOLOGY ANALYSIS REPORT\n")
        f.write("=" * 80 + "\n\n")
        
        for idx, item in enumerate(results, 1):
            f.write(f"Button #{idx}: {item['label']}\n")
            f.write(f"Source URL: {item['root_target_url']}\n")
            f.write("-" * 50 + "\n")
            
            details = item["details"]
            if not details or not details["streams"]:
                f.write("Result: No direct stream URLs found or page offline.\n\n")
                continue
                
            f.write(f"Tech Classified: {details['type'].upper()}\n")
            f.write(f"Player Framework: {details['player']}\n")
            
            f.write("Direct Stream URLs Found:\n")
            for s in details["streams"]:
                f.write(f"  - {s}\n")
                
            if details["clear_keys"]:
                f.write("DRM ClearKeys Found:\n")
                for kid, key in details["clear_keys"].items():
                    f.write(f"  - Key ID (KID): {kid}\n    Key Value:   {key}\n")
                    
            f.write("\nCopy-Pasteable Integration Code:\n")
            f.write("`" * 3 + "html\n")
            # Generate code block
            main_stream = details["streams"][0]
            if details["type"] == "dash" and details["clear_keys"]:
                snippet = make_dash_snippet(main_stream, details["clear_keys"])
            else:
                snippet = make_hls_snippet(main_stream)
            f.write(snippet + "\n")
            f.write("`" * 3 + "\n\n")
            f.write("=" * 80 + "\n\n")

def main():
    parser = argparse.ArgumentParser(
        description="Automated crawler to extract streaming links, detect technology (HLS/DASH), and output Shaka/HLS player code."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-u", "--url",
        help="A single root URL to crawl (e.g. epicsports.mobi/p/page.html)."
    )
    group.add_argument(
        "-f", "--file",
        help="Path to a text file containing one root URL per line."
    )
    parser.add_argument(
        "-o", "--output",
        default="streaming_report.txt",
        help="Path to save the output text report (default: streaming_report.txt)."
    )
    parser.add_argument(
        "--json",
        help="Path to save output as raw JSON data."
    )
    parser.add_argument(
        "-d", "--depth",
        type=int,
        default=2,
        help="Maximum recursion depth for following iframes/redirects (default: 2)."
    )
    
    args = parser.parse_args()
    
    # Collect root URLs
    root_urls = []
    if args.url:
        root_urls.append(args.url)
    elif args.file:
        if not os.path.exists(args.file):
            print(f"[-] Error: File '{args.file}' not found.", file=sys.stderr)
            sys.exit(1)
        with open(args.file, "r") as f:
            root_urls = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
            
    compiled_results = []
    for root_url in root_urls:
        res = process_root_url(root_url, max_depth=args.depth)
        compiled_results.extend(res)
        
    # Save Report
    save_text_report(compiled_results, args.output)
    print(f"\n[+] Analysis complete! Formatted report saved to: {args.output}")
    
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(compiled_results, f, indent=4)
        print(f"[+] Raw JSON data saved to: {args.json}")

if __name__ == "__main__":
    main()
