#!/usr/bin/env python3
import os
import re
import sys
import json
import argparse
import requests
from urllib.parse import urljoin, urlparse, parse_qs
from html.parser import HTMLParser

# ----------------------------------------------------------------------
# HTML Link Parser
# ----------------------------------------------------------------------
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

# ----------------------------------------------------------------------
# HTML Player Template with Shaka Player DRM Support Integrated
# ----------------------------------------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1">
<title>Live Player</title>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=Inter:wght@400;500&display=swap" rel="stylesheet">

<!-- Shaka Player (DASH + HLS native) -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.7.11/shaka-player.compiled.min.js"></script>
<!-- HLS.js fallback -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.4.10/hls.min.js"></script>

<!-- ======= AD HEAD CODE ======= -->
<script src="https://throughalivemedication.com/78/95/36/78953660b707ff1c75b91b933c958645.js"></script>

<style>
:root {
  --red: #e63946;
  --dark: #0a0a0f;
  --card: #111118;
  --border: rgba(255,255,255,0.07);
  --text: #f0f0f0;
  --muted: #666;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background: var(--dark);
  color: var(--text);
  font-family: 'Inter', sans-serif;
  min-height: 100vh;
}
.site-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: #0d0d14;
  border-bottom: 1px solid var(--border);
}
.logo {
  font-family: 'Rajdhani', sans-serif;
  font-size: 22px;
  font-weight: 700;
  color: #fff;
  text-decoration: none;
  letter-spacing: 1px;
}
.logo span { color: var(--red); }
.header-right { display: flex; align-items: center; gap: 10px; }
.live-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(230,57,70,0.12);
  border: 1px solid rgba(230,57,70,0.3);
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
  color: var(--red);
  letter-spacing: 1px;
}
.live-badge .dot {
  width: 7px; height: 7px;
  background: var(--red);
  border-radius: 50%;
  animation: blink 1.2s infinite;
}
/* Engine badge */
.engine-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 20px;
  letter-spacing: 0.6px;
  font-family: 'Rajdhani', sans-serif;
  transition: all 0.3s;
}
.engine-badge.dash  { background: rgba(52,152,219,0.15); color: #3498db; border: 1px solid rgba(52,152,219,0.3); }
.engine-badge.hls   { background: rgba(46,204,113,0.15); color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }
.engine-badge.mp4   { background: rgba(155,89,182,0.15); color: #9b59b6; border: 1px solid rgba(155,89,182,0.3); }
.engine-badge.iframe{ background: rgba(241,196,15,0.15);  color: #f1c40f;  border: 1px solid rgba(241,196,15,0.3); }
.engine-badge.none  { background: rgba(255,255,255,0.05); color: #666;    border: 1px solid rgba(255,255,255,0.1); }

@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.2} }
.alert-bar {
  background: linear-gradient(90deg, #1a0a0a, #1f0d0d, #1a0a0a);
  border-bottom: 1px solid rgba(230,57,70,0.2);
  padding: 8px 16px;
  text-align: center;
  font-size: 12.5px;
  color: #ccc;
}
.alert-bar strong { color: var(--red); }
.alert-bar a { color: #f4c430; text-decoration: none; font-weight: 600; margin: 0 4px; }
.ad-top { text-align:center; padding: 6px 0; background:#0d0d14; }
.main { max-width: 960px; margin: 0 auto; padding: 16px 12px; }

/* ── Player card ── */
.player-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.video-wrap {
  position: relative;
  width: 100%;
  aspect-ratio: 16/9;
  background: #000;
}
video { width:100%; height:100%; display:block; background:#000; }

/* iframe mode */
.iframe-wrap {
  position: absolute;
  inset: 0;
  display: none;
  z-index: 5;
}
.iframe-wrap iframe {
  width: 100%;
  height: 100%;
  border: none;
  display: block;
}
.iframe-wrap.active { display: block; }

/* controls */
.controls-bar {
  position: absolute;
  bottom: 0; left: 0; right: 0;
  background: linear-gradient(transparent, rgba(0,0,0,0.85));
  padding: 30px 14px 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  opacity: 0;
  transition: opacity 0.25s;
  z-index: 10;
}
.video-wrap:hover .controls-bar,
.video-wrap.show-controls .controls-bar { opacity: 1; }
/* hide controls in iframe mode */
.video-wrap.iframe-mode .controls-bar { display: none; }

.ctrl-btn {
  background: none;
  border: none;
  color: #fff;
  cursor: pointer;
  padding: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: background 0.15s;
}
.ctrl-btn:hover { background: rgba(255,255,255,0.12); }
.ctrl-btn svg { width:20px; height:20px; fill:currentColor; }
.progress-wrap {
  flex: 1;
  height: 4px;
  background: rgba(255,255,255,0.2);
  border-radius: 2px;
  cursor: pointer;
}
.progress-bar {
  height: 100%;
  background: var(--red);
  border-radius: 2px;
  width: 0%;
  transition: width 0.5s linear;
}
.vol-wrap { display: flex; align-items: center; gap: 6px; }
input[type=range].vol-slider {
  width: 60px; height: 3px;
  accent-color: var(--red);
  cursor: pointer;
}
.quality-select {
  background: rgba(255,255,255,0.1);
  border: 1px solid rgba(255,255,255,0.15);
  color: #fff;
  font-size: 11px;
  padding: 3px 6px;
  border-radius: 4px;
  cursor: pointer;
  outline: none;
}
.quality-select option { background: #111; color: #fff; }
.time-label {
  font-size: 11px;
  color: rgba(255,255,255,0.7);
  font-family: 'Rajdhani', sans-serif;
  letter-spacing: 0.5px;
  white-space: nowrap;
}

/* overlays */
.overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  z-index: 20;
  background: rgba(0,0,0,0.85);
  text-align: center;
  padding: 20px;
}
.overlay.hidden { display: none; }
.spinner {
  width: 48px; height: 48px;
  border: 3px solid rgba(230,57,70,0.2);
  border-top-color: var(--red);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.overlay p { font-size: 13px; color: #aaa; }
.overlay .sub { font-size: 11px; color: #555; margin-top: -8px; }
.error-icon { font-size: 40px; }
.error-title { font-size: 17px; font-weight: 600; color: var(--red); font-family: 'Rajdhani', sans-serif; }
.retry-btn {
  margin-top: 4px;
  padding: 8px 24px;
  background: var(--red);
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  font-family: 'Rajdhani', sans-serif;
  letter-spacing: 0.5px;
  transition: opacity 0.2s;
}
.retry-btn:hover { opacity: 0.85; }
.engine-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: -4px;
}
.engine-try {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 4px;
  font-family: 'Rajdhani', sans-serif;
  font-weight: 600;
  letter-spacing: 0.4px;
  background: rgba(255,255,255,0.05);
  color: #555;
  border: 1px solid rgba(255,255,255,0.08);
  transition: all 0.3s;
}
.engine-try.trying  { color: #f39c12; border-color: rgba(243,156,18,0.4); background: rgba(243,156,18,0.08); }
.engine-try.success { color: #2ecc71; border-color: rgba(46,204,113,0.4); background: rgba(46,204,113,0.08); }
.engine-try.failed  { color: #555;    border-color: rgba(255,255,255,0.06); text-decoration: line-through; }

.player-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
  gap: 8px;
}
.stream-status {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  color: #aaa;
}
.stream-status .sdot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: #555;
  flex-shrink: 0;
}
.stream-status.live   .sdot { background: #2ecc71; animation: blink 1.2s infinite; }
.stream-status.error  .sdot { background: var(--red); }
.stream-status.buffer .sdot { background: #f39c12; animation: blink 0.6s infinite; }
.notice { font-size: 12px; color: #f39c12; }

/* ── STREAM LINKS ── */
.stream-links-section {
  margin-top: 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.stream-links-header {
  padding: 10px 14px;
  font-family: 'Rajdhani', sans-serif;
  font-size: 13px;
  font-weight: 600;
  color: #888;
  letter-spacing: 0.8px;
  text-transform: uppercase;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.type-legend {
  display: flex;
  gap: 6px;
}
.stream-links-list { display: flex; flex-direction: column; }
.stream-link-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
  transition: background 0.15s;
  text-decoration: none;
}
.stream-link-item:last-child { border-bottom: none; }
.stream-link-item:hover { background: rgba(255,255,255,0.04); }
.stream-link-item.active { background: rgba(230,57,70,0.08); border-left: 3px solid var(--red); }
.stream-link-item.disabled { opacity: 0.4; pointer-events: none; }
.link-num {
  font-family: 'Rajdhani', sans-serif;
  font-size: 13px;
  font-weight: 700;
  color: var(--red);
  min-width: 22px;
}
.link-info { flex: 1; }
.link-label { font-size: 13px; font-weight: 500; color: var(--text); display: block; }
.link-meta  { font-size: 11px; color: #666; margin-top: 2px; display: block; }
.link-badges { display: flex; gap: 5px; flex-wrap: wrap; }
.badge {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  font-family: 'Rajdhani', sans-serif;
  letter-spacing: 0.3px;
}
.badge.hd    { background: rgba(46,204,113,0.15);  color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }
.badge.sd    { background: rgba(241,196,15,0.15);   color: #f1c40f; border: 1px solid rgba(241,196,15,0.3); }
.badge.eng   { background: rgba(52,152,219,0.15);   color: #3498db; border: 1px solid rgba(52,152,219,0.3); }
.badge.ara   { background: rgba(155,89,182,0.15);   color: #9b59b6; border: 1px solid rgba(155,89,182,0.3); }
.badge.ios   { background: rgba(255,255,255,0.08);  color: #ccc;    border: 1px solid rgba(255,255,255,0.15); }
.badge.auto  { background: rgba(230,57,70,0.12);    color: var(--red); border: 1px solid rgba(230,57,70,0.25); }
.badge.dash  { background: rgba(52,152,219,0.12);   color: #3498db; border: 1px solid rgba(52,152,219,0.3); }
.badge.hls   { background: rgba(46,204,113,0.12);   color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }
.badge.mp4   { background: rgba(155,89,182,0.12);   color: #9b59b6; border: 1px solid rgba(155,89,182,0.3); }
.badge.iframe{ background: rgba(241,196,15,0.12);   color: #f1c40f; border: 1px solid rgba(241,196,15,0.3); }
.link-play-icon { color: #444; transition: color 0.15s; }
.stream-link-item:hover .link-play-icon,
.stream-link-item.active .link-play-icon { color: var(--red); }
.link-play-icon svg { width: 18px; height: 18px; fill: currentColor; }

.ad-mid { text-align:center; margin: 14px 0; }
.socials { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; }
.soc-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 6px;
  font-size: 12.5px;
  font-weight: 600;
  text-decoration: none;
  color: #fff;
  font-family: 'Rajdhani', sans-serif;
  transition: opacity 0.2s;
}
.soc-btn:hover { opacity: 0.85; }
.soc-btn.wa  { background: #128C7E; }
.soc-btn.wac { background: #25D366; }
.soc-btn.tg  { background: #0088cc; }
.disclaimer {
  background: rgba(255,255,255,0.02);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
  font-size: 11.5px;
  color: #555;
  line-height: 1.7;
  margin: 14px 0;
}
.ad-bottom { text-align:center; padding: 14px 0; }

.embed-box {
  margin-top: 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px;
}
.embed-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  font-family: 'Rajdhani', sans-serif;
  font-size: 13px;
  font-weight: 600;
  color: #888;
  letter-spacing: 0.8px;
  text-transform: uppercase;
}
.copy-embed-btn {
  background: var(--red);
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  font-family: 'Rajdhani', sans-serif;
  transition: background 0.2s;
}
.copy-embed-btn:hover { background: #b52a35; }
.embed-input {
  width: 100%;
  background: rgba(0,0,0,0.5);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: #ccc;
  padding: 8px 10px;
  font-size: 12px;
  outline: none;
  font-family: monospace;
}

@media(max-width:600px) {
  .site-header { padding: 10px 14px; }
  .logo { font-size: 18px; }
  input[type=range].vol-slider { width: 44px; }
  .time-label { display: none; }
  .link-label { font-size: 12px; }
  .type-legend { display: none; }
}
</style>
</head>
<body>

<header class="site-header">
  <a href="#" class="logo">WORLD<span>CUP</span></a>
  <div class="header-right">
    <span class="engine-badge none" id="engine-badge">DETECTING</span>
    <div class="live-badge"><div class="dot"></div> LIVE</div>
  </div>
</header>

<div class="alert-bar">
  <strong>🛑 ALERT</strong> — Wait <strong>20 seconds</strong> for the stream to load.
  Join our <a href="https://whatsapp.com/channel/0029VbBrULX30LKYjSCtrT10" target="_blank">WhatsApp Group</a> for daily live links 👇
</div>

<div class="ad-top"><!-- TOP AD CODE HERE --></div>

<div class="main">

  <div class="player-card">
    <div class="video-wrap" id="vwrap">

      <!-- Native video element (used by DASH / HLS / MP4) -->
      <video id="video" playsinline autoplay muted></video>

      <!-- iframe container (used when URL is an embed) -->
      <div class="iframe-wrap" id="iframe-wrap">
        <iframe id="iframe-player"
          allowfullscreen
          allow="autoplay; encrypted-media; picture-in-picture"
          sandbox="allow-scripts allow-same-origin allow-presentation allow-popups"
          referrerpolicy="no-referrer"></iframe>
      </div>

      <!-- Loading overlay -->
      <div class="overlay" id="ov-load">
        <div class="spinner"></div>
        <p id="ov-load-msg">Detecting stream type...</p>
        <div class="engine-list" id="engine-list"></div>
      </div>

      <!-- Error overlay -->
      <div class="overlay hidden" id="ov-err">
        <div class="error-icon">⚠️</div>
        <div class="error-title">Stream Error</div>
        <p id="err-msg">Could not load the stream.</p>
        <button class="retry-btn" id="retry-btn">▶ Try Next Link</button>
      </div>

      <!-- No URL overlay -->
      <div class="overlay hidden" id="ov-none">
        <div class="error-icon">📺</div>
        <div class="error-title">No Stream Link</div>
        <p>Pass a stream URL via <code style="color:#f4c430">?url=</code> or click a link below.</p>
        <p style="font-size:11px; color:#555; margin-top:4px;">Supports .mpd · .m3u8 · .mp4/.webm · iframe embeds</p>
      </div>

      <!-- Controls (hidden in iframe mode) -->
      <div class="controls-bar" id="cbar">
        <button class="ctrl-btn" id="btn-play" title="Play/Pause">
          <svg id="ico-play"  viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
          <svg id="ico-pause" viewBox="0 0 24 24" style="display:none"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
        </button>

        <div class="progress-wrap">
          <div class="progress-bar" id="prog-bar"></div>
        </div>

        <span class="time-label" id="time-lbl">● LIVE</span>

        <div class="vol-wrap">
          <button class="ctrl-btn" id="btn-mute" title="Mute">
            <svg id="ico-vol"  viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0014 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>
            <svg id="ico-mute" viewBox="0 0 24 24" style="display:none"><path d="M16.5 12A4.5 4.5 0 0014 7.97v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>
          </button>
          <input type="range" class="vol-slider" id="vol-slider" min="0" max="1" step="0.05" value="1">
        </div>

        <select class="quality-select" id="quality-sel" title="Quality">
          <option value="-1">Auto</option>
        </select>

        <button class="ctrl-btn" id="btn-fs" title="Fullscreen">
          <svg id="ico-fs" viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
          <svg id="ico-ex" viewBox="0 0 24 24" style="display:none"><path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>
        </button>
      </div>
    </div><!-- /video-wrap -->

    <div class="player-info">
      <div class="stream-status" id="sstatus">
        <div class="sdot"></div>
        <span id="stext">Initializing player...</span>
      </div>
      <div class="notice">⏳ Wait 20 sec for stream to load properly</div>
    </div>
  </div><!-- /player-card -->

  <!-- ══ STREAM LINKS ── -->
  <div class="stream-links-section" id="links-section">
    <div class="stream-links-header">
      <span>📡 Available Streams</span>
      <div class="type-legend">
        <span class="badge dash">DASH</span>
        <span class="badge hls">HLS</span>
        <span class="badge mp4">MP4</span>
        <span class="badge iframe">EMBED</span>
      </div>
    </div>
    <div class="stream-links-list" id="links-list"></div>
  </div>

  <!-- ══ EMBED PLAYER ── -->
  <div class="embed-box" id="embed-section">
    <div class="embed-header">
      <span>🔗 Embed Code</span>
      <button class="copy-embed-btn" id="copy-embed-btn">📋 Copy Code</button>
    </div>
    <input type="text" class="embed-input" id="embed-input" readonly value="">
  </div>

  <!-- Popup Ad -->
  <div class="ad-mid">
    <div id="popup-ad-overlay" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.6); z-index:99999; justify-content:center; align-items:center;">
      <div style="position:relative; background:#fff; padding:10px; border-radius:8px;">
        <button onclick="document.getElementById('popup-ad-overlay').style.display='none'" style="position:absolute; top:-12px; right:-12px; background:#333; color:#fff; border:none; border-radius:50%; width:26px; height:26px; font-size:16px; cursor:pointer; line-height:1;">&times;</button>
        <script>
          atOptions = { 'key':'26752c18ca8361bba098d31342583042', 'format':'iframe', 'height':250, 'width':300, 'params':{} };
        </script>
        <script src="https://www.highperformanceformat.com/26752c18ca8361bba098d31342583042/invoke.js"></script>
      </div>
    </div>
    <script>
      window.addEventListener('load', function() {
        setTimeout(function() { document.getElementById('popup-ad-overlay').style.display = 'flex'; }, 3000);
      });
    </script>
  </div>

  <div class="socials">
    <a href="https://whatsapp.com/channel/0029VbBrULX30LKYjSCtrT10" target="_blank" rel="noopener" class="soc-btn wa">💬 WhatsApp Group</a>
    <a href="YOUR_WHATSAPP_CHANNEL_LINK" target="_blank" rel="noopener" class="soc-btn wac">📢 WhatsApp Channel</a>
    <a href="https://t.me/+3Xrk9OJsuT44YjQ1" target="_blank" rel="noopener" class="soc-btn tg">✈️ Telegram</a>
  </div>

  <div class="disclaimer">
    This site does not host any media files. All streams are sourced from third-party external services.
    We are not responsible for externally hosted content. All trademarks, videos, and logos belong to their respective owners.
  </div>

  <div class="ad-bottom">
    <script src="https://throughalivemedication.com/78/95/36/78953660b707ff1c75b91b933c958645.js"></script>
  </div>

</div><!-- /main -->

<script>
function initPlayerSystem() {
  try {
/* ═══════════════════════════════════════════════════════════════
   STREAM LINKS CONFIG
═══════════════════════════════════════════════════════════════ */
##STREAM_LINKS_PLACEHOLDER##

// Initialize links status tracking
STREAM_LINKS.forEach((lnk, i) => {
  lnk.id = i;
  lnk.failCount = 0;
  lnk.success = false;
});

function sortAndRebuildLinks() {
  const activeId = STREAM_LINKS[activeIndex] ? STREAM_LINKS[activeIndex].id : null;
  
  STREAM_LINKS.sort((a, b) => {
    const aFailed = a.failCount > 0;
    const bFailed = b.failCount > 0;
    
    // Put failed ones at the bottom, sorted by failCount ascending
    if (aFailed && !bFailed) return 1;
    if (!aFailed && bFailed) return -1;
    if (aFailed && bFailed) {
      if (a.failCount !== b.failCount) {
        return a.failCount - b.failCount;
      }
    }
    
    // Put successful ones at the top
    if (a.success && !b.success) return -1;
    if (!a.success && b.success) return 1;
    
    // Keep original python priority
    return a.id - b.id;
  });
  
  if (activeId !== null) {
    activeIndex = STREAM_LINKS.findIndex(l => l.id === activeId);
  }
  
  buildLinks();
  if (activeIndex !== -1) {
    setActive(activeIndex);
  }
}

/* ═══════════════════════════════════════════════════════════════
   ENGINE DETECTION
═══════════════════════════════════════════════════════════════ */
const TYPE_DASH   = 'dash';
const TYPE_HLS    = 'hls';
const TYPE_NATIVE = 'native';
const TYPE_IFRAME = 'iframe';

function detectType(url, override) {
  if (override && override !== 'auto') return override;
  if (!url) return null;

  const u = url.split('?')[0].toLowerCase();

  if (u.endsWith('.html') || u.endsWith('.htm') || u.endsWith('.php') || u.endsWith('.jsp') || u.endsWith('.asp')) {
    return TYPE_IFRAME;
  }

  if (u.endsWith('.mpd')  || u.includes('.mpd?') || u.includes('manifest.mpd') || u.includes('/dash/')) return TYPE_DASH;
  if (u.endsWith('.m3u8') || u.includes('.m3u8?') || u.includes('/hls/')  || u.includes('playlist.m3u8')) return TYPE_HLS;
  if (u.endsWith('.mp4')  || u.endsWith('.webm') || u.endsWith('.ogg') || u.endsWith('.ts') || u.endsWith('.mkv')) return TYPE_NATIVE;

  if (override === 'iframe' || looksLikeEmbed(url)) return TYPE_IFRAME;

  return 'unknown';
}

function looksLikeEmbed(url) {
  const u = url.toLowerCase();
  return (
    u.includes('/embed') || u.includes('/player') || u.includes('/live/') ||
    u.includes('youtube') || u.includes('dailymotion') || u.includes('twitch') ||
    u.includes('vimeo') || u.includes('facebook') || u.includes('streamable') ||
    u.includes('ok.ru') || u.includes('rutube') || u.includes('odysee') ||
    (!url.split('?')[0].match(/\.(mpd|m3u8|mp4|webm|ogg|ts|mkv|flv|avi)$/i))
  );
}

/* ═══════════════════════════════════════════════════════════════
   DOM REFS
═══════════════════════════════════════════════════════════════ */
const video       = document.getElementById('video');
const vwrap       = document.getElementById('vwrap');
const iframeWrap  = document.getElementById('iframe-wrap');
const iframeEl    = document.getElementById('iframe-player');
const ovLoad      = document.getElementById('ov-load');
const ovLoadMsg   = document.getElementById('ov-load-msg');
const ovErr       = document.getElementById('ov-err');
const ovNone      = document.getElementById('ov-none');
const errMsg      = document.getElementById('err-msg');
const sstatus     = document.getElementById('sstatus');
const stext       = document.getElementById('stext');
const progBar     = document.getElementById('prog-bar');
const timeLbl     = document.getElementById('time-lbl');
const btnPlay     = document.getElementById('btn-play');
const icoPlay     = document.getElementById('ico-play');
const icoPause    = document.getElementById('ico-pause');
const btnMute     = document.getElementById('btn-mute');
const icoVol      = document.getElementById('ico-vol');
const icoMute     = document.getElementById('ico-mute');
const volSlider   = document.getElementById('vol-slider');
const qualSel     = document.getElementById('quality-sel');
const btnFs       = document.getElementById('btn-fs');
const icoFs       = document.getElementById('ico-fs');
const icoEx       = document.getElementById('ico-ex');
const retryBtn    = document.getElementById('retry-btn');
const linksList   = document.getElementById('links-list');
const engineBadge = document.getElementById('engine-badge');
const engineList  = document.getElementById('engine-list');

let shakaPlayer  = null;
let hlsInstance  = null;
let activeIndex  = -1;

/* ═══════════════════════════════════════════════════════════════
   ENGINE BADGE UI
═══════════════════════════════════════════════════════════════ */
function setEngineBadge(type) {
  const map = {
    dash  : ['DASH',   'dash'],
    hls   : ['HLS',    'hls'],
    native: ['MP4',    'mp4'],
    iframe: ['EMBED',  'iframe'],
    none  : ['—',      'none'],
  };
  const [label, cls] = map[type] || ['DETECT', 'none'];
  engineBadge.textContent = label;
  engineBadge.className = 'engine-badge ' + cls;
}

function buildEngineTries(types) {
  engineList.innerHTML = '';
  types.forEach(t => {
    const el = document.createElement('span');
    el.className = 'engine-try';
    el.id = 'etry-' + t;
    el.textContent = t.toUpperCase();
    engineList.appendChild(el);
  });
}
function setEngineTry(type, state) {
  const el = document.getElementById('etry-' + type);
  if (el) el.className = 'engine-try ' + state;
}

/* ═══════════════════════════════════════════════════════════════
   CLEANUP
═══════════════════════════════════════════════════════════════ */
function destroyAll() {
  if (shakaPlayer) { shakaPlayer.destroy(); shakaPlayer = null; }
  if (hlsInstance)  { hlsInstance.destroy(); hlsInstance = null; }
  video.pause();
  video.src = '';
  video.removeAttribute('src');
  video.load();
  iframeEl.src = '';
  iframeWrap.classList.remove('active');
  vwrap.classList.remove('iframe-mode');
  qualSel.innerHTML = '<option value="-1">Auto</option>';
}

/* ═══════════════════════════════════════════════════════════════
   STATUS HELPERS
═══════════════════════════════════════════════════════════════ */
function setStatus(state, msg) {
  stext.textContent = msg;
  sstatus.className = 'stream-status ' + state;
}
let autoswitchTimeout = null;

function showError(msg) {
  ovLoad.classList.add('hidden');
  errMsg.textContent = msg || 'Stream could not be loaded.';
  ovErr.classList.remove('hidden');
  
  // Find next link before sorting
  let nextIdx = -1;
  let nextId = null;
  
  let nextUntried = STREAM_LINKS.findIndex((l, i) => i !== activeIndex && l.failCount === 0 && l.url);
  if (nextUntried !== -1) {
    nextIdx = nextUntried;
  } else {
    let minFail = Infinity;
    let bestIdx = -1;
    STREAM_LINKS.forEach((l, i) => {
      if (i !== activeIndex && l.url) {
        if (l.failCount < minFail) {
          minFail = l.failCount;
          bestIdx = i;
        }
      }
    });
    nextIdx = bestIdx;
  }
  
  if (nextIdx !== -1) {
    nextId = STREAM_LINKS[nextIdx].id;
  }

  // Update failure score
  const activeLnk = STREAM_LINKS[activeIndex];
  if (activeLnk) {
    activeLnk.failCount++;
    activeLnk.success = false;
    sortAndRebuildLinks();
  }

  // Find the new index of next after sorting
  let finalNextIdx = -1;
  if (nextId !== null) {
    finalNextIdx = STREAM_LINKS.findIndex(l => l.id === nextId);
  }

  retryBtn.textContent = finalNextIdx !== -1 ? '▶ Try Next Link' : '🔄 Refresh';
  setStatus('error', 'Stream error');
  setEngineBadge('none');
  
  if (finalNextIdx !== -1) {
    clearTimeout(autoswitchTimeout);
    ovLoadMsg.textContent = 'Stream error. Autoswitching to next link...';
    ovLoad.classList.remove('hidden');
    ovErr.classList.add('hidden');
    autoswitchTimeout = setTimeout(() => {
      switchStream(finalNextIdx);
    }, 3000);
  }
}

/* ═══════════════════════════════════════════════════════════════
   IFRAME MODE
 ═══════════════════════════════════════════════════════════════ */
function loadIframe(url) {
  buildEngineTries(['iframe']);
  setEngineTry('iframe', 'trying');
  ovLoadMsg.textContent = 'Loading embed...';

  iframeEl.src = url;
  iframeWrap.classList.add('active');
  vwrap.classList.add('iframe-mode');

  iframeEl.onload = () => {
    ovLoad.classList.add('hidden');
    setEngineTry('iframe', 'success');
    setEngineBadge('iframe');
    setStatus('live', 'Embed loaded');
    
    // Mark success
    const activeLnk = STREAM_LINKS[activeIndex];
    if (activeLnk) {
      activeLnk.success = true;
      activeLnk.failCount = 0;
      sortAndRebuildLinks();
    }
  };
  iframeEl.onerror = () => {
    showError('Could not load the embed. Try another link.');
    setEngineTry('iframe', 'failed');
  };
  setTimeout(() => {
    if (!ovLoad.classList.contains('hidden')) {
      ovLoad.classList.add('hidden');
      setEngineBadge('iframe');
      setStatus('live', 'Embed loaded');
      setEngineTry('iframe', 'success');
      
      const activeLnk = STREAM_LINKS[activeIndex];
      if (activeLnk) {
        activeLnk.success = true;
        activeLnk.failCount = 0;
        sortAndRebuildLinks();
      }
    }
  }, 4000);
}

/* ═══════════════════════════════════════════════════════════════
   NATIVE HTML5 MODE
═══════════════════════════════════════════════════════════════ */
function loadNative(url) {
  buildEngineTries(['native']);
  setEngineTry('native', 'trying');
  ovLoadMsg.textContent = 'Loading video...';

  video.src = url;
  video.play().catch(() => {});
}

/* ═══════════════════════════════════════════════════════════════
   HLS MODE (HLS.js with native fallback for Safari/iOS)
═══════════════════════════════════════════════════════════════ */
function loadHLS(url, onSuccess, onFail) {
  setEngineTry('hls', 'trying');
  ovLoadMsg.textContent = 'Connecting HLS stream...';

  if (!Hls.isSupported() && video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = url;
    video.play().catch(() => {});
    setEngineTry('hls', 'success');
    setEngineBadge('hls');
    if (onSuccess) onSuccess();
    return;
  }

  if (!Hls.isSupported()) {
    setEngineTry('hls', 'failed');
    if (onFail) onFail('HLS not supported in this browser');
    return;
  }

  hlsInstance = new Hls({
    maxBufferLength: 30,
    maxMaxBufferLength: 60,
    liveSyncDurationCount: 3,
    liveMaxLatencyDurationCount: 6,
    enableWorker: true,
    lowLatencyMode: false,
  });

  hlsInstance.loadSource(url);
  hlsInstance.attachMedia(video);

  hlsInstance.on(Hls.Events.MANIFEST_PARSED, (e, data) => {
    populateQualities(data.levels);
    video.play().catch(() => {});
    setEngineTry('hls', 'success');
    setEngineBadge('hls');
    if (onSuccess) onSuccess();
  });

  hlsInstance.on(Hls.Events.ERROR, (e, data) => {
    if (data.fatal) {
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        setStatus('buffer', 'Network error, retrying...');
        hlsInstance.startLoad();
      } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        setStatus('buffer', 'Media error, recovering...');
        hlsInstance.recoverMediaError();
      } else {
        hlsInstance.destroy(); hlsInstance = null;
        setEngineTry('hls', 'failed');
        if (onFail) onFail('HLS fatal error');
        else showError('HLS stream failed. Try another link.');
      }
    }
  });
}

/* ═══════════════════════════════════════════════════════════════
   SHAKA (DASH + HLS with DRM ClearKey support)
═══════════════════════════════════════════════════════════════ */
async function loadShaka(url, mimeHint, onSuccess, onFail) {
  setEngineTry('shaka', 'trying');
  ovLoadMsg.textContent = 'Connecting DASH stream...';

  if (!shaka || !shaka.Player || !shaka.Player.isBrowserSupported()) {
    setEngineTry('shaka', 'failed');
    if (onFail) onFail('Shaka not supported');
    return;
  }

  shaka.polyfill.installAll();
  shakaPlayer = new shaka.Player(video);

  const shakaConfig = {
    streaming: {
      bufferingGoal: 30,
      rebufferingGoal: 2,
      bufferBehind: 30,
    }
  };

  // Configure ClearKeys DRM if present in configuration
  const activeLink = STREAM_LINKS[activeIndex];
  if (activeLink && activeLink.clearKeys && Object.keys(activeLink.clearKeys).length > 0) {
    shakaConfig.drm = {
      clearKeys: activeLink.clearKeys
    };
  }

  shakaPlayer.configure(shakaConfig);

  shakaPlayer.addEventListener('error', (e) => {
    console.error('Shaka error:', e.detail);
    if (shakaPlayer) { shakaPlayer.destroy(); shakaPlayer = null; }
    setEngineTry('shaka', 'failed');
    if (onFail) onFail('Shaka: ' + (e.detail ? e.detail.message : 'error'));
  });

  shakaPlayer.addEventListener('buffering', (e) => {
    if (e.buffering) setStatus('buffer', 'Buffering...');
    else setStatus('live', 'Stream live');
  });

  try {
    await shakaPlayer.load(url);
    populateShakaQualities();
    video.play().catch(() => {});
    setEngineTry('shaka', 'success');
    setEngineBadge('dash');
    if (onSuccess) onSuccess();
  } catch (e) {
    if (shakaPlayer) { shakaPlayer.destroy(); shakaPlayer = null; }
    setEngineTry('shaka', 'failed');
    if (onFail) onFail('Shaka load failed: ' + e.message);
  }
}

function populateShakaQualities() {
  if (!shakaPlayer) return;
  const tracks = shakaPlayer.getVariantTracks();
  qualSel.innerHTML = '<option value="-1">Auto</option>';
  const seen = new Set();
  tracks.forEach(t => {
    if (t.height && !seen.has(t.height)) {
      seen.add(t.height);
      const o = document.createElement('option');
      o.value = t.id;
      o.textContent = t.height + 'p';
      qualSel.appendChild(o);
    }
  });
}

qualSel.addEventListener('change', () => {
  const val = parseInt(qualSel.value);
  if (shakaPlayer) {
    if (val === -1) {
      shakaPlayer.configure({ abr: { enabled: true } });
    } else {
      shakaPlayer.configure({ abr: { enabled: false } });
      shakaPlayer.selectVariantTrack(shakaPlayer.getVariantTracks().find(t => t.id === val), true);
    }
  } else if (hlsInstance) {
    hlsInstance.currentLevel = val;
  }
});

function populateQualities(levels) {
  qualSel.innerHTML = '<option value="-1">Auto</option>';
  levels.forEach((l, i) => {
    if (l.height) {
      const o = document.createElement('option');
      o.value = i;
      o.textContent = l.height + 'p';
      qualSel.appendChild(o);
    }
  });
}

/* ═══════════════════════════════════════════════════════════════
   UNKNOWN-TYPE: WATERFALL FALLBACK
═══════════════════════════════════════════════════════════════ */
function loadUnknown(url) {
  buildEngineTries(['shaka', 'hls', 'native', 'iframe']);
  ovLoadMsg.textContent = 'Trying all engines...';

  loadShaka(url, null,
    () => {},
    () => {
      if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
      video.src = ''; video.load();
      loadHLS(url,
        () => {},
        () => {
          setEngineTry('native', 'trying');
          video.src = url;
          video.play().catch(() => {
            setEngineTry('native', 'failed');
            video.src = '';
            loadIframe(url);
          });
        }
      );
    }
  );
}

/* ═══════════════════════════════════════════════════════════════
   MAIN DISPATCH
═══════════════════════════════════════════════════════════════ */
function initPlayer(url, typeOverride) {
  if (!url) {
    ovLoad.classList.add('hidden');
    ovNone.classList.remove('hidden');
    setStatus('', 'No stream URL');
    return;
  }

  const type = detectType(url, typeOverride);
  ovErr.classList.add('hidden');
  ovNone.classList.add('hidden');
  ovLoad.classList.remove('hidden');
  setStatus('buffer', 'Connecting...');

  switch (type) {
    case TYPE_DASH:
      buildEngineTries(['shaka']);
      loadShaka(url, 'application/dash+xml',
        () => {},
        (e) => showError('DASH failed: ' + e)
      );
      break;
    case TYPE_HLS:
      buildEngineTries(['hls']);
      loadHLS(url, null, (e) => showError('HLS failed: ' + e));
      break;
    case TYPE_NATIVE:
      loadNative(url);
      break;
    case TYPE_IFRAME:
      loadIframe(url);
      break;
    default:
      loadUnknown(url);
      break;
  }
}

/* ═══════════════════════════════════════════════════════════════
   VIDEO EVENTS
═══════════════════════════════════════════════════════════════ */
function unmuteAndPlay() {
  video.muted = false; video.volume = 1;
  volSlider.value = 1; updateVolIcon();
}
function updateVolIcon() {
  const muted = video.muted || video.volume == 0;
  icoVol.style.display  = muted ? 'none'  : 'block';
  icoMute.style.display = muted ? 'block' : 'none';
}

video.addEventListener('play',  () => { icoPlay.style.display='none'; icoPause.style.display='block'; });
video.addEventListener('pause', () => { icoPlay.style.display='block'; icoPause.style.display='none'; });
video.addEventListener('waiting', () => { ovLoad.classList.remove('hidden'); ovLoadMsg.textContent = 'Buffering...'; setStatus('buffer', 'Buffering...'); });
video.addEventListener('canplay', () => { ovLoad.classList.add('hidden'); });
video.addEventListener('playing', () => {
  ovLoad.classList.add('hidden');
  ovErr.classList.add('hidden');
  setStatus('live', 'Stream live');
  unmuteAndPlay();
  if (!shakaPlayer && !hlsInstance) {
    setEngineBadge('native');
    setEngineTry('native', 'success');
  }
  
  // Mark success
  const activeLnk = STREAM_LINKS[activeIndex];
  if (activeLnk) {
    activeLnk.success = true;
    activeLnk.failCount = 0;
    sortAndRebuildLinks();
  }
});
video.addEventListener('stalled', () => setStatus('buffer', 'Stream stalled...'));
video.addEventListener('error',   () => {
  if (!shakaPlayer && !hlsInstance) {
    showError('Video error. Try another link.');
  }
});

const LIVE_THRESHOLD = 3600 * 10;
function isLive() {
  return !isFinite(video.duration) || video.duration > LIVE_THRESHOLD;
}

video.addEventListener('timeupdate', () => {
  if (isLive()) {
    progBar.style.width = '100%';
    timeLbl.textContent = '● LIVE';
  } else {
    const pct = (video.currentTime / video.duration) * 100;
    progBar.style.width = pct + '%';
    const fmt = s => String(Math.floor(s/60)).padStart(2,'0')+':'+String(Math.floor(s%60)).padStart(2,'0');
    timeLbl.textContent = fmt(video.currentTime) + ' / ' + fmt(video.duration);
  }
});

/* ═══════════════════════════════════════════════════════════════
   CONTROLS
═══════════════════════════════════════════════════════════════ */
btnPlay.addEventListener('click', () => { video.paused ? video.play() : video.pause(); });
volSlider.addEventListener('input', () => {
  video.volume = volSlider.value;
  video.muted = volSlider.value == 0;
  updateVolIcon();
});
btnMute.addEventListener('click', () => {
  video.muted = !video.muted;
  volSlider.value = video.muted ? 0 : video.volume || 1;
  updateVolIcon();
});
btnFs.addEventListener('click', () => {
  document.fullscreenElement ? document.exitFullscreen() : vwrap.requestFullscreen();
});
document.addEventListener('fullscreenchange', () => {
  const fs = !!document.fullscreenElement;
  icoFs.style.display = fs ? 'none'  : 'block';
  icoEx.style.display = fs ? 'block' : 'none';
});
vwrap.addEventListener('touchstart', () => {
  vwrap.classList.add('show-controls');
  clearTimeout(vwrap._ct);
  vwrap._ct = setTimeout(() => vwrap.classList.remove('show-controls'), 3000);
});

/* ═══════════════════════════════════════════════════════════════
   RETRY
═══════════════════════════════════════════════════════════════ */
retryBtn.addEventListener('click', () => {
  let nextIdx = -1;
  let nextUntried = STREAM_LINKS.findIndex((l, i) => i !== activeIndex && l.failCount === 0 && l.url);
  if (nextUntried !== -1) {
    nextIdx = nextUntried;
  } else {
    let minFail = Infinity;
    let bestIdx = -1;
    STREAM_LINKS.forEach((l, i) => {
      if (i !== activeIndex && l.url) {
        if (l.failCount < minFail) {
          minFail = l.failCount;
          bestIdx = i;
        }
      }
    });
    nextIdx = bestIdx;
  }
  if (nextIdx !== -1) switchStream(nextIdx);
  else location.reload();
});

/* ═══════════════════════════════════════════════════════════════
   LINK LIST UI
═══════════════════════════════════════════════════════════════ */
const BADGE_LABELS = {
  hd:'HD', sd:'SD', eng:'ENG', ara:'ARA', ios:'🍎 iPhone',
  auto:'AUTO', dash:'DASH', hls:'HLS', mp4:'MP4', iframe:'EMBED'
};

function buildLinks() {
  linksList.innerHTML = '';
  STREAM_LINKS.forEach((lnk, i) => {
    const row = document.createElement('div');
    row.className = 'stream-link-item' + (!lnk.url ? ' disabled' : '');
    row.dataset.index = i;

    const displayBadges = [...lnk.badges];
    const detectedType = lnk.type !== 'auto' ? lnk.type : detectType(lnk.url, null);
    if (detectedType && !displayBadges.includes(detectedType) && detectedType !== 'unknown' && detectedType !== null) {
      displayBadges.unshift(detectedType);
    }

    const badgeHTML = displayBadges.map(b => `<span class="badge ${b}">${BADGE_LABELS[b] || b.toUpperCase()}</span>`).join('');

    row.innerHTML = `
      <span class="link-num">${i + 1}</span>
      <span class="link-info">
        <span class="link-label">${lnk.label}</span>
        <span class="link-meta">${lnk.meta}</span>
      </span>
      <span class="link-badges">${badgeHTML}</span>
      <span class="link-play-icon">
        <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
      </span>
    `;

    if (lnk.url) row.addEventListener('click', () => switchStream(i));
    linksList.appendChild(row);
  });
}

function setActive(idx) {
  activeIndex = idx;
  document.querySelectorAll('.stream-link-item').forEach((el, i) => {
    el.classList.toggle('active', i === idx);
  });
}

function switchStream(idx) {
  const lnk = STREAM_LINKS[idx];
  if (!lnk || !lnk.url) return;
  setActive(idx);
  destroyAll();
  setEngineBadge('none');
  ovErr.classList.add('hidden');
  ovNone.classList.add('hidden');
  ovLoad.classList.remove('hidden');
  ovLoadMsg.textContent = 'Detecting stream type...';
  engineList.innerHTML = '';
  setStatus('buffer', 'Connecting...');
  initPlayer(lnk.url, lnk.type);
}

/* ═══════════════════════════════════════════════════════════════
   BOOTSTRAP
═══════════════════════════════════════════════════════════════ */
buildLinks();

const params     = new URL(location.href).searchParams;
const paramUrl   = params.get('url');
const paramType  = params.get('type') || 'auto';

if (paramUrl) {
  const matchIdx = STREAM_LINKS.findIndex(l => l.url === paramUrl);
  if (matchIdx !== -1) setActive(matchIdx);
  initPlayer(paramUrl, paramType);
} else {
  const firstIdx = STREAM_LINKS.findIndex(l => l.url);
  if (firstIdx !== -1) {
    switchStream(firstIdx);
  } else {
    ovLoad.classList.add('hidden');
    ovNone.classList.remove('hidden');
    setStatus('', 'No stream URL');
  }
}
  } catch (e) {
    console.error(e);
    const errDiv = document.createElement('div');
    errDiv.style = "color:red; background:#fff; padding:20px; position:fixed; bottom:0; left:0; width:100%; z-index:999999; border-top:5px solid red; font-family:monospace; font-size:12px; overflow:auto; max-height:200px;";
    errDiv.innerHTML = '<strong>JavaScript Error:</strong> ' + e.message + '<br><pre>' + e.stack + '</pre>';
    document.body.appendChild(errDiv);
  }
}
function bootstrapPlayer() {
  const l = document.getElementById('links-list');
  const v = document.getElementById('video');
  
  let diag = document.getElementById('diag-debug');
  if (!diag) {
    diag = document.createElement('div');
    diag.id = 'diag-debug';
    diag.style = 'background:yellow; color:black; padding:10px; position:fixed; top:0; left:0; z-index:9999999; font-size:12px; font-family:monospace; border:1px solid #000;';
    document.body.appendChild(diag);
  }
  diag.innerHTML = 'Debug — LinksList: ' + (l ? 'FOUND' : 'NULL') + ' | Video: ' + (v ? 'FOUND' : 'NULL');

  if (l && v) {
    diag.innerHTML += ' => Initializing...';
    setTimeout(() => {
      if (diag) diag.style.display = 'none'; // hide debug banner after successful init
    }, 2000);
    initPlayerSystem();
  } else {
    setTimeout(bootstrapPlayer, 100);
  }
}
bootstrapPlayer();
</script>
</body>
</html>"""

# ----------------------------------------------------------------------
# Crawler Logic
# ----------------------------------------------------------------------
def is_match_page_url(url, text):
    u = url.lower()
    t = text.lower()
    # Exclude profile, labels or feed URLs
    if any(p in u for p in ["/privacy", "/contact", "/about", "/disclaimer", "/terms", "/search/label", "feed", "blogger.com", "whatsapp.com", "t.me", "telegram"]):
        return False
    # Check for match indicators
    match_indicators = ["vs", " v ", "live", "watch", "stream", "score", "match", "friendly", "telecast", "preview", "lineup"]
    if any(ind in u or ind in t for ind in match_indicators):
        if any(p in u for p in ["/2025/", "/2026/", "/p/"]):
            return True
    return False

def get_match_pages_from_root(root_url, html):
    parser = EpicLinkParser(root_url)
    parser.feed(html)
    
    url_to_texts = {}
    for item in parser.results:
        if item["tag"] == "a":
            u = item["url"]
            t = item["text"].strip()
            if t:
                if u not in url_to_texts:
                    url_to_texts[u] = []
                if t not in url_to_texts[u]:
                    url_to_texts[u].append(t)
                    
    match_pages = []
    for u, texts in url_to_texts.items():
        is_match = False
        for t in texts:
            if is_match_page_url(u, t):
                is_match = True
                break
        if is_match:
            time_str = None
            for t in texts:
                if re.search(r'\b\d{1,2}:\d{2}\s*(?:AM|PM)?\b', t, re.IGNORECASE):
                    time_str = t
                    break
            
            combined_label = " | ".join(texts)
            match_pages.append({
                "url": u,
                "texts": texts,
                "time_str": time_str,
                "label": combined_label
            })
    return match_pages

def parse_match_time(time_str):
    from datetime import datetime, time, timedelta
    match = re.search(r'(\d{1,2}):(\d{2})\s*(AM|PM)?', time_str, re.IGNORECASE)
    if not match:
        return None
    
    hour = int(match.group(1))
    minute = int(match.group(2))
    ampm = match.group(3)
    
    if ampm:
        ampm = ampm.upper()
        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
            
    now = datetime.now()
    match_dt = datetime.combine(now.date(), time(hour, minute))
    
    diff = match_dt - now
    if diff.total_seconds() < -43200:
        match_dt += timedelta(days=1)
    elif diff.total_seconds() > 43200:
        match_dt -= timedelta(days=1)
        
    return match_dt

def is_match_active(time_str):
    from datetime import datetime
    if not time_str:
        return True
        
    dt = parse_match_time(time_str)
    if not dt:
        return True
        
    now = datetime.now()
    diff = dt - now
    diff_minutes = diff.total_seconds() / 60.0
    
    # Active if starting within 10 minutes OR started up to 3 hours (180 minutes) ago
    if -180.0 <= diff_minutes <= 10.0:
        return True
    return False

def extract_match_name(text_or_url):
    # Match strings like Spain vs Peru or France v Northern Ireland
    match = re.search(r'([a-zA-Z0-9\s\.\-]+?\s+(?:vs|v\.?)\s+[a-zA-Z0-9\s\.\-]+)', text_or_url, re.IGNORECASE)
    if match:
        name = match.group(1).strip()
        # Clean up double spaces, trailing words
        name = re.sub(r'\s+', ' ', name)
        name = re.sub(r'(?i)\b(live|score|preview|lineup|telecast|details|stream|free|online|watch|hd|sd|link)\b.*', '', name).strip()
        return name
    return None

def get_clean_match_title(label, url):
    if not url:
        return None
    # 1. Try to extract from label
    match_name = extract_match_name(label)
    if match_name:
        return match_name
        
    # 2. Try to extract from URL (path)
    parsed = urlparse(url)
    path_segment = parsed.path
    if path_segment.lower().endswith(".html"):
        path_segment = path_segment[:-5]
    elif path_segment.lower().endswith(".htm"):
        path_segment = path_segment[:-4]
        
    path_segment = path_segment.replace("-", " ").replace("_", " ")
    match_name = extract_match_name(path_segment)
    if match_name:
        return match_name.title()
        
    # 3. Fall back
    return None

def is_likely_stream_button(text, url, parent_url):
    u = url.lower()
    t = text.lower()
    
    # Exclude social/template links
    if any(social in u for social in ["whatsapp.com", "t.me", "telegram.me", "facebook.com", "twitter.com", "instagram.com", "pinterest.com", "linkedin.com", "tumblr.com", "blogger.com/profile", "google.com", "themexpose", "gooyaabi"]):
        return False
        
    # Exclude pages like Contact, About, Privacy
    if any(p in u for p in ["/privacy", "/contact", "/about", "/disclaimer", "/terms"]):
        return False
        
    # Exclude typical search query templates
    if "/search?q=" in u:
        return False
        
    # If the URL is a dated post on the same or related blog, it is likely a match preview link, not a stream button
    is_dated_post = bool(re.search(r'/\d{4}/\d{2}/', u))
    if is_dated_post:
        # Check if the text is a short stream button label (e.g. <= 80 chars and has button words)
        is_short_label = len(t) <= 80 and any(kw in t for kw in ["link", "stream", "watch", "live", "play", "channel", "ios", "android"])
        if not is_short_label:
            return False
            
    # The text must contain stream keywords
    keywords = ["link", "stream", "watch", "live", "tv", "channel", "player", "android", "ios", "click"]
    if any(k in t for k in keywords):
        return True
        
    return False

def extract_root_links(root_url, headers=None):
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
    
    matched_links = []
    for item in parser.results:
        text = item["text"]
        link_url = item["url"]
        
        if is_likely_stream_button(text, link_url, root_url) and item["tag"] in ("a", "button"):
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
    
    # Extract streams from query parameters of the current page URL (e.g. ?url=https://...)
    parsed_url = urlparse(url)
    q_params = parse_qs(parsed_url.query)
    for q_name, q_vals in q_params.items():
        for val in q_vals:
            if val.startswith("http") and (".m3u8" in val.lower() or ".mpd" in val.lower()):
                if val not in stream_info["streams"]:
                    stream_info["streams"].append(val)
                    if ".m3u8" in val.lower() and stream_info["type"] == "unknown":
                        stream_info["type"] = "hls"
                        stream_info["player"] = "hls.js / video.js"
                    elif ".mpd" in val.lower() and stream_info["type"] == "unknown":
                        stream_info["type"] = "dash"
                        stream_info["player"] = "shaka"
    
    m3u8_links = re.findall(r"[\x27\"](https?://[^\x27\"]+\.m3u8[^\x27\"]*)[\x27\"]", html, re.IGNORECASE)
    mpd_links = re.findall(r"[\x27\"](https?://[^\x27\"]+\.mpd[^\x27\"]*)[\x27\"]", html, re.IGNORECASE)
    
    keys_match = re.search(r"clearKeys\s*:\s*\{([^}]+)\}", html, re.DOTALL)
    if keys_match:
        pairs = re.findall(r"[\x27\"]([0-9a-fA-F]{32})[\x27\"]\s*:\s*[\x27\"]([0-9a-fA-F]{32})[\x27\"]", keys_match.group(1))
        if pairs:
            stream_info["clear_keys"] = dict(pairs)
            stream_info["player"] = "shaka"
            stream_info["type"] = "dash"
            
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
                
    for link in m3u8_links:
        parsed_link = urlparse(link)
        link_path = parsed_link.path.lower()
        if any(link_path.endswith(ext) for ext in [".html", ".htm", ".php", ".jsp", ".asp"]):
            continue
        if ".js" not in link.lower() and link not in stream_info["streams"]:
            stream_info["streams"].append(link)
            if stream_info["type"] == "unknown":
                stream_info["type"] = "hls"
                stream_info["player"] = "hls.js / video.js"

    for link in mpd_links:
        parsed_link = urlparse(link)
        link_path = parsed_link.path.lower()
        if any(link_path.endswith(ext) for ext in [".html", ".htm", ".php", ".jsp", ".asp"]):
            continue
        if ".js" not in link.lower() and link not in stream_info["streams"]:
            stream_info["streams"].append(link)
            if stream_info["type"] == "unknown":
                stream_info["type"] = "dash"
                if stream_info["player"] == "unknown":
                    stream_info["player"] = "shaka"

    parser = EpicLinkParser(url)
    parser.feed(html)
    
    iframes = [item["url"] for item in parser.results if item["tag"] == "iframe"]
    for iframe_url in iframes:
        if iframe_url not in visited and iframe_url != url:
            stream_info["nested_links"].append({
                "type": "iframe",
                "url": iframe_url
            })
            
    for item in parser.results:
        if item["tag"] == "a":
            text = item["text"]
            link_url = item["url"]
            if is_likely_stream_button(text, link_url, url) and link_url not in visited and link_url != url:
                stream_info["nested_links"].append({
                    "type": "iframe",
                    "url": link_url
                })
            
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
        print(f"{'  ' * depth}[-]" + f" Failed to fetch {url}: {e}", file=sys.stderr)
        return {
            "url": url,
            "error": str(e),
            "nested_results": []
        }
        
    info = analyze_page(url, html, visited)
    info["nested_results"] = []
    
    has_redirect = any(item["type"] == "js_map_redirect" for item in info["nested_links"])
    
    for nested in info["nested_links"]:
        should_follow = False
        if nested["type"] == "iframe":
            should_follow = True
        elif nested["type"] == "js_map_redirect":
            should_follow = True
        elif not has_redirect and nested["type"].startswith("js_map_"):
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
    
    # Fallback to iframes if no direct streams found
    if not streams:
        def find_all_iframes(node):
            if not node:
                return []
            iframes = []
            for link in node.get("nested_links", []):
                if link.get("type") == "iframe":
                    iframes.append(link.get("url"))
            for nested in node.get("nested_results", []):
                iframes.extend(find_all_iframes(nested))
            return list(dict.fromkeys(iframes))
            
        iframes = find_all_iframes(tree)
        if iframes:
            streams = iframes
            stream_type = "iframe"
            player = "iframe"
            
    return {
        "streams": streams,
        "clear_keys": clear_keys,
        "player": player,
        "type": stream_type
    }

def process_root_url(root_url, max_depth=2):
    print(f"\n[*] STEP 1: Scraping page for stream links: {root_url}")
    matched_links = extract_root_links(root_url)
    print(f"[+] Found {len(matched_links)} stream button(s)/link(s).")
    
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
            "root_origin_url": root_url,
            "details": details
        })
        
    return results

# ----------------------------------------------------------------------
# Main Builder Execution
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Scrapes live streaming root URLs, extracts links, crawls subpages, and generates a fully updated HTML Player."
    )
    parser.add_argument(
        "-u", "--urls",
        nargs="*",
        help="One or more root page URLs to crawl (separated by space)."
    )
    parser.add_argument(
        "-f", "--file",
        help="Path to a text file containing root URLs (one per line)."
    )
    parser.add_argument(
        "-o", "--output",
        default="player.html",
        help="Path where the final HTML player file should be written (default: player.html)."
    )
    parser.add_argument(
        "-d", "--depth",
        type=int,
        default=2,
        help="Maximum recursion depth for following iframes/redirects (default: 2)."
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously in a daemon/loop mode."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Sleep interval in minutes between pipeline runs in loop mode (default: 5)."
    )
    
    args = parser.parse_args()
    
    # Collect root URLs
    root_urls = []
    if args.urls:
        root_urls.extend(args.urls)
    if args.file:
        if os.path.exists(args.file):
            with open(args.file, "r") as f:
                root_urls.extend([line.strip() for line in f if line.strip() and not line.strip().startswith("#")])
        else:
            print(f"[-] Error: File '{args.file}' not found.", file=sys.stderr)
            sys.exit(1)
            
    if not root_urls:
        root_urls.append("https://www.epicsports.mobi/p/most-expensive-fifa-world-cups-in.html")
        
    print(f"[*] Processing {len(root_urls)} root URL(s)...")
    
    import time
    import html

    # Standalone Embed Player Template
    EMBED_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1">
<title>{title}</title>
<!-- Shaka Player (DASH + HLS native) -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.7.11/shaka-player.compiled.min.js"></script>
<!-- HLS.js fallback -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.4.10/hls.min.js"></script>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { width:100%; height:100%; background:#000; overflow:hidden; font-family:sans-serif; }
  #video-wrapper { width:100%; height:100%; position:relative; display:flex; align-items:center; justify-content:center; }
  video { width:100%; height:100%; object-fit:contain; background:#000; }
  iframe { width:100%; height:100%; border:none; background:#000; }
  .overlay {
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    background: rgba(10,10,15,0.9); color: #fff; z-index: 10; font-size: 16px; transition: opacity 0.5s;
  }
  .spinner {
    width: 50px; height: 50px; border: 3px solid rgba(255,255,255,0.1);
    border-radius: 50%; border-top-color: #e63946; animation: spin 1s ease-in-out infinite; margin-bottom: 15px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .hidden { opacity: 0; pointer-events: none; }
</style>
</head>
<body>
<div id="video-wrapper">
  <div id="overlay-load" class="overlay">
    <div class="spinner"></div>
    <div id="load-msg">Loading stream...</div>
  </div>
  <div id="overlay-error" class="overlay hidden">
    <div style="color:#e63946; font-size:24px; margin-bottom:10px;">⚠ Playback Error</div>
    <div id="err-msg">Stream could not be loaded.</div>
  </div>
  <video id="video" controls autoplay playsinline></video>
  <div id="iframe-wrap" style="display:none; width:100%; height:100%;">
    <iframe id="iframe-el" allow="autoplay; encrypted-media" allowfullscreen></iframe>
  </div>
</div>
<script>
  const streamUrl = {url_json};
  const streamType = {type_json};
  const clearKeys = {keys_json};

  const video = document.getElementById('video');
  const overlayLoad = document.getElementById('overlay-load');
  const overlayError = document.getElementById('overlay-error');
  const loadMsg = document.getElementById('load-msg');
  const errMsg = document.getElementById('err-msg');
  const iframeWrap = document.getElementById('iframe-wrap');
  const iframeEl = document.getElementById('iframe-el');

  let shakaPlayer = null;
  let hlsInstance = null;

  function initPlayer(url, type) {
    if (type === 'iframe') {
      video.style.display = 'none';
      iframeWrap.style.display = 'block';
      iframeEl.src = url;
      overlayLoad.classList.add('hidden');
      return;
    }

    if (type === 'dash') {
      if (shaka.Player.isBrowserSupported()) {
        shakaPlayer = new shaka.Player(video);
        shakaPlayer.addEventListener('error', (e) => {
          console.error("Shaka error", e);
          showError("DASH Player Error: " + e.detail.code);
        });
        
        if (clearKeys && Object.keys(clearKeys).length > 0) {
          shakaPlayer.configure({
            drm: { clearKeys: clearKeys }
          });
        }

        shakaPlayer.load(url).then(() => {
          overlayLoad.classList.add('hidden');
          video.play().catch(()=>{});
        }).catch((e) => {
          console.error("Shaka load error", e);
          showError("Could not load DASH manifest.");
        });
      } else {
        showError("DASH is not supported by this browser.");
      }
    } else if (type === 'hls') {
      if (Hls.isSupported()) {
        hlsInstance = new Hls({ maxMaxBufferLength: 10 });
        hlsInstance.loadSource(url);
        hlsInstance.attachMedia(video);
        hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => {
          overlayLoad.classList.add('hidden');
          video.play().catch(()=>{});
        });
        hlsInstance.on(Hls.Events.ERROR, (event, data) => {
          if (data.fatal) {
            console.error("HLS fatal error", data);
            showError("HLS fatal playback error.");
          }
        });
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = url;
        video.addEventListener('loadedmetadata', () => {
          overlayLoad.classList.add('hidden');
          video.play().catch(()=>{});
        });
        video.addEventListener('error', () => {
          showError("Native HLS playback error.");
        });
      } else {
        showError("HLS is not supported by this browser.");
      }
    } else {
      video.src = url;
      video.addEventListener('loadedmetadata', () => {
        overlayLoad.classList.add('hidden');
        video.play().catch(()=>{});
      });
      video.addEventListener('error', () => {
        showError("Native HTML5 playback error.");
      });
    }
  }

  function showError(msg) {
    overlayLoad.classList.add('hidden');
    errMsg.textContent = msg;
    overlayError.classList.remove('hidden');
  }

  initPlayer(streamUrl, streamType);
</script>
</body>
</html>"""

    # Iframes Index Page Template
    IFRAMES_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Embeddable Player Iframes</title>
<style>
  body {{ font-family: sans-serif; background: #0a0a0f; color: #f0f0f0; padding: 20px; }}
  h1, h2, h3 {{ color: #e63946; }}
  .card {{ background: #111118; border: 1px solid rgba(255,255,255,0.07); padding: 15px; margin-bottom: 20px; border-radius: 8px; }}
  code {{ display: block; background: #000; padding: 10px; border-radius: 4px; border: 1px solid #333; color: #50fa7b; overflow-x: auto; white-space: pre-wrap; word-break: break-all; }}
</style>
</head>
<body>
  <h1>Embeddable Player Iframes</h1>
  <p>Use the following iframe codes to embed the live streams on other websites. The master player automatically updates as matches change.</p>
  
  <div class="card">
    <h2>1. Master Interactive Player</h2>
    <p>This player includes the channel sidebar, auto-failover, and time-based match listings. It automatically updates in real-time as new matches start.</p>
    <code>&lt;iframe src="player.html" width="100%" height="600px" frameborder="0" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen&gt;&lt;/iframe&gt;</code>
  </div>

  <h2>2. Individual Direct Streams</h2>
  <div id="streams-list">
    {stream_iframes}
  </div>
</body>
</html>"""

    while True:
        print(f"\n==================================================")
        print(f"[*] Pipeline iteration started at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"==================================================")
        
        # Clear previously generated files
        import shutil
        output_dir = os.path.dirname(os.path.abspath(args.output))
        embeds_dir = os.path.join(output_dir, "embeds")
        iframes_html_path = os.path.join(output_dir, "iframes.html")
        
        print("[*] Clearing previously generated files...")
        for path in [args.output, iframes_html_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"[-] Warning: could not remove {path}: {e}")
                    
        if os.path.exists(embeds_dir):
            try:
                shutil.rmtree(embeds_dir)
            except Exception as e:
                print(f"[-] Warning: could not remove directory {embeds_dir}: {e}")
        
        expanded_root_urls = []
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        
        for url in root_urls:
            try:
                print(f"[*] Checking if root URL is a match portal: {url}")
                response = requests.get(url, headers=headers, timeout=15)
                response.raise_for_status()
                match_pages = get_match_pages_from_root(url, response.text)
                if match_pages:
                    print(f"[+] Found {len(match_pages)} total match page(s) on portal. Filtering by time...")
                    active_count = 0
                    for mp in match_pages:
                        time_str = mp.get("time_str")
                        # Check scheduling proximity
                        if is_match_active(time_str):
                            active_count += 1
                            time_lbl = f" [{time_str}]" if time_str else ""
                            print(f"  - ACTIVE: {mp['label']}{time_lbl} -> {mp['url']}")
                            if mp['url'] not in expanded_root_urls:
                                expanded_root_urls.append(mp['url'])
                        else:
                            print(f"  - SKIPPED (not starting soon/active): {mp['label']} [{time_str}] -> {mp['url']}")
                    print(f"[+] Added {active_count} active match page(s) out of {len(match_pages)}.")
                else:
                    print(f"[+] No portal sub-pages found. Processing directly.")
                    if url not in expanded_root_urls:
                        expanded_root_urls.append(url)
            except Exception as e:
                print(f"[-] Warning: Failed to pre-scan root URL {url}: {e}")
                if url not in expanded_root_urls:
                    expanded_root_urls.append(url)
                    
        print(f"[*] Total target URL(s) to process after time-filtering: {len(expanded_root_urls)}")
        
        # Process and crawl all streams
        all_results = []
        for root_url in expanded_root_urls:
            results = process_root_url(root_url, max_depth=args.depth)
            all_results.extend(results)
        
        # Format results to JavaScript objects for STREAM_LINKS with sorting by priority (DASH first, then HLS, etc.)
        resolved_items = []
        seen_urls = set()
        
        for res in all_results:
            label_raw = res["label"]
            details = res["details"]
            
            if not details or not details["streams"]:
                print(f"[-] Skipping {label_raw}: No stream URL resolved.")
                continue
                
            for s_idx, stream_url in enumerate(details["streams"], 1):
                if stream_url in seen_urls:
                    print(f"[-] Skipping duplicate stream URL for: {label_raw} (Stream {s_idx})")
                    continue
                seen_urls.add(stream_url)
                
                # Determine type of this specific stream
                url_lower = stream_url.lower()
                if ".mpd" in url_lower:
                    s_type = "dash"
                elif ".m3u8" in url_lower:
                    s_type = "hls"
                elif any(ext in url_lower for ext in [".mp4", ".webm", ".ogg", ".ts", ".mkv"]):
                    s_type = "native"
                else:
                    s_type = "iframe"
                    
                # Clear keys only for DASH
                s_keys = details["clear_keys"] if s_type == "dash" else {}
                
                # Parse badges from label contents or types
                badges = [s_type]
                label_lower = label_raw.lower()
                if "hd" in label_lower or "hd" in stream_url.lower():
                    badges.append("hd")
                if "sd" in label_lower:
                    badges.append("sd")
                if "eng" in label_lower or "english" in label_lower:
                    badges.append("eng")
                if "ara" in label_lower or "arabic" in label_lower:
                    badges.append("ara")
                if "ios" in label_lower or "ios" in stream_url.lower() or "iphone" in label_lower:
                    badges.append("ios")
                    
                badges = list(dict.fromkeys(badges))
                
                # Construct meta descriptive line
                meta_parts = []
                if s_type == "dash":
                    meta_parts.append("MPEG-DASH")
                elif s_type == "hls":
                    meta_parts.append("HLS")
                elif s_type == "iframe":
                    meta_parts.append("HTML5 Embed")
                else:
                    meta_parts.append(s_type.upper())
                    
                meta_parts.append("Auto Quality")
                
                if "eng" in badges:
                    meta_parts.append("English Audio")
                elif "ara" in badges:
                    meta_parts.append("Arabic Audio")
                    
                if s_keys:
                    meta_parts.append("DRM ClearKey Protected")
                    
                resolved_items.append({
                    "label_raw": label_raw,
                    "root_target_url": res["root_target_url"],
                    "root_origin_url": res.get("root_origin_url"),
                    "stream_url": stream_url,
                    "stream_type": s_type,
                    "clear_keys": s_keys,
                    "badges": badges,
                    "meta_parts": meta_parts
                })

        # Sort resolved streams stably: DASH (0) -> HLS (1) -> Native (2) -> Iframe/Embed (3) -> Unknown (4)
        def get_type_priority(item):
            t = item["stream_type"]
            if t == "dash":
                return 0
            elif t == "hls":
                return 1
            elif t == "native":
                return 2
            elif t == "iframe":
                return 3
            return 4

        resolved_items.sort(key=get_type_priority)

        # Label and build the final STREAM_LINKS array
        stream_links_js = []
        for idx, item in enumerate(resolved_items, 1):
            label_raw = item["label_raw"]
            stream_url = item["stream_url"]
            stream_type = item["stream_type"]
            clear_keys = item["clear_keys"]
            badges = item["badges"]
            meta_parts = item["meta_parts"]
            
            # Clean labels
            match_title = get_clean_match_title(label_raw, item.get("root_origin_url") or item["root_target_url"])
            if match_title:
                clean_label = f"Link {idx} — {match_title}"
            else:
                if " | " in label_raw:
                    parts = label_raw.split(" | ")
                    if len(parts) >= 2:
                        clean_label = f"Link {idx} — {parts[1]}"
                elif label_raw.lower().startswith("link"):
                    clean_label = re.sub(r"^Link\s*\d+", f"Link {idx}", label_raw, flags=re.IGNORECASE)
                else:
                    clean_label = f"Link {idx} — {label_raw}"
                
            meta_str = " · ".join(meta_parts)
            
            js_obj = {
                "label": clean_label,
                "meta": meta_str,
                "badges": badges,
                "type": stream_type,
                "url": stream_url
            }
            if clear_keys:
                js_obj["clearKeys"] = clear_keys
                
            stream_links_js.append(js_obj)
            
        # Serialize Python dictionary to JavaScript array format
        js_array_str = "const STREAM_LINKS = " + json.dumps(stream_links_js, indent=2) + ";"
        
        # Replace placeholder inside HTML Template
        output_html = HTML_TEMPLATE.replace("##STREAM_LINKS_PLACEHOLDER##", js_array_str)
        
        # Write output HTML file
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output_html)
            print(f"\n[+] SUCCESS: HTML Player generated successfully!")
            print(f"[+] Output written to: {os.path.abspath(args.output)}")
        except Exception as e:
            print(f"[-] Error writing output HTML file: {e}", file=sys.stderr)
            
        # Generate standalone embeds and iframes.html
        output_dir = os.path.dirname(os.path.abspath(args.output))
        embeds_dir = os.path.join(output_dir, "embeds")
        os.makedirs(embeds_dir, exist_ok=True)
        
        iframe_rows = []
        for idx, item in enumerate(stream_links_js, 1):
            url = item["url"]
            stype = item["type"]
            keys = item.get("clearKeys", {})
            label = item["label"]
            
            # Write individual embed HTML file
            filename = f"embed_{idx}.html"
            filepath = os.path.join(embeds_dir, filename)
            
            content = EMBED_TEMPLATE.replace("{url_json}", json.dumps(url))
            content = content.replace("{type_json}", json.dumps(stype))
            content = content.replace("{keys_json}", json.dumps(keys))
            content = content.replace("{title}", label)
            
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
                
            iframe_tag = f'<iframe src="embeds/embed_{idx}.html" width="100%" height="450px" frameborder="0" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>'
            
            iframe_rows.append(f"""
            <div class="card">
              <h3>{html.escape(label)}</h3>
              <p>Format: {html.escape(item['meta'])}</p>
              <code>{html.escape(iframe_tag)}</code>
            </div>
            """)
            
        # Write iframes.html
        iframes_html_path = os.path.join(output_dir, "iframes.html")
        iframes_content = IFRAMES_PAGE_TEMPLATE.format(stream_iframes="\n".join(iframe_rows))
        try:
            with open(iframes_html_path, "w", encoding="utf-8") as f:
                f.write(iframes_content)
            print(f"[+] SUCCESS: Iframes listing written to: {os.path.abspath(iframes_html_path)}")
        except Exception as e:
            print(f"[-] Error writing iframes listing: {e}", file=sys.stderr)
            
        if not args.loop:
            break
            
        print(f"\n[*] Daemon mode active: sleeping for {args.interval} minute(s)...")
        time.sleep(args.interval * 60)

if __name__ == "__main__":
    main()
