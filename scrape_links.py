#!/usr/bin/env python3
import os
import re
import sys
import json
import csv
import argparse
import requests
from urllib.parse import urljoin
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
        if tag in ("a", "button"):
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
            elif "onclick" in self.current_attrs:
                onclick_val = self.current_attrs["onclick"]
                # Match single/double quoted http/https URLs
                match = re.search(r"'(https?://[^'\s]+)'|\"(https?://[^\"]+)\"", onclick_val)
                if match:
                    url = match.group(1) or match.group(2)
            
            if url:
                # Resolve relative URLs to absolute URLs
                resolved_url = urljoin(self.base_url, url)
                self.results.append({
                    "text": text.replace("\n", " ").strip(),
                    "url": resolved_url,
                    "tag": tag
                })
            
            self.current_tag = None
            self.current_attrs = {}
            self.current_text = []

def scrape_url(url, pattern=None, keyword=None, match_all=False, headers=None):
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"[-] Error fetching {url}: {e}", file=sys.stderr)
        return []

    parser = EpicLinkParser(url)
    parser.feed(response.text)
    
    filtered_results = []
    
    # Compile regex pattern if provided
    re_pattern = None
    if pattern:
        try:
            re_pattern = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            print(f"[-] Invalid regex pattern '{pattern}': {e}. Using substring match instead.", file=sys.stderr)
            
    for item in parser.results:
        text = item["text"]
        link_url = item["url"]
        
        # Decide if match
        is_match = False
        if match_all:
            is_match = True
        elif re_pattern:
            if re_pattern.search(text) or re_pattern.search(link_url):
                is_match = True
        elif keyword:
            kw = keyword.lower()
            if kw in text.lower() or kw in link_url.lower():
                is_match = True
        else:
            # Default behavior: match if text starts with "link"
            if text.lower().startswith("link"):
                is_match = True
                
        if is_match:
            filtered_results.append(item)
            
    return filtered_results

def main():
    parser = argparse.ArgumentParser(
        description="Scrape stream and broadcast links from web pages (supporting standard links and JavaScript buttons)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-u", "--url",
        help="A single URL to scrape links from."
    )
    group.add_argument(
        "-f", "--file",
        help="Path to a text file containing one URL per line."
    )
    parser.add_argument(
        "-p", "--pattern",
        default="^link",
        help="Regex pattern to filter links by their anchor text/display text (default: matches text starting with 'link')."
    )
    parser.add_argument(
        "-k", "--keyword",
        help="Filter links by a simple case-insensitive keyword (searches both text and URL)."
    )
    parser.add_argument(
        "-a", "--all",
        action="store_true",
        help="Extract all links/buttons on the page without any text filters."
    )
    parser.add_argument(
        "-o", "--output",
        choices=["json", "csv", "text"],
        help="File format to save results. If specified, saves to 'scraped_links.<format>' unless --out-file is specified."
    )
    parser.add_argument(
        "--out-file",
        help="Custom file path/name to save results."
    )
    
    args = parser.parse_args()
    
    # Determine the list of URLs to process
    urls = []
    if args.url:
        urls.append(args.url)
    elif args.file:
        if not os.path.exists(args.file):
            print(f"[-] Error: File '{args.file}' not found.", file=sys.stderr)
            sys.exit(1)
        with open(args.file, "r") as f:
            urls = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    all_scraped = {}
    total_found = 0
    
    print(f"[*] Starting scrape of {len(urls)} source URL(s)...")
    
    for idx, url in enumerate(urls, 1):
        print(f"[*] Processing [{idx}/{len(urls)}]: {url}")
        # If default pattern is used and user wants "all" or "keyword", we pass pattern=None
        pattern_arg = None if (args.all or args.keyword) else args.pattern
        
        links = scrape_url(
            url,
            pattern=pattern_arg,
            keyword=args.keyword,
            match_all=args.all
        )
        
        all_scraped[url] = links
        total_found += len(links)
        print(f"[+] Found {len(links)} matching link(s) on {url}")
        for link in links:
            print(f"    - [{link['tag'].upper()}] {link['text']} -> {link['url']}")
            
    print(f"\n[*] Scrape complete. Total links extracted: {total_found}")
    
    # Save output if requested
    if args.output or args.out_file:
        fmt = args.output or (args.out_file.split(".")[-1] if "." in args.out_file else "json")
        out_path = args.out_file or f"scraped_links.{fmt}"
        
        # Flatten structure for CSV/Text if multiple URLs, or keep hierarchical
        flattened = []
        for src_url, links in all_scraped.items():
            for link in links:
                flattened.append({
                    "source_url": src_url,
                    "tag": link["tag"],
                    "text": link["text"],
                    "url": link["url"]
                })
                
        try:
            if fmt == "json":
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(all_scraped, f, indent=4)
            elif fmt == "csv":
                with open(out_path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=["source_url", "tag", "text", "url"])
                    writer.writeheader()
                    writer.writerows(flattened)
            else: # Text format
                with open(out_path, "w", encoding="utf-8") as f:
                    for src_url, links in all_scraped.items():
                        f.write(f"Source URL: {src_url}\n")
                        f.write("=" * 80 + "\n")
                        for link in links:
                            f.write(f"[{link['tag'].upper()}] {link['text']}\n  Link: {link['url']}\n\n")
                        f.write("\n")
            print(f"[+] Saved results to {out_path}")
        except Exception as e:
            print(f"[-] Error writing output file: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
