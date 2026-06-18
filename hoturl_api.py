#!/usr/bin/env python3
"""Hot URL API — Submit source URLs for live/upcoming matches on the fly.

Run:  python3 hoturl_api.py
Port: 8899 (configurable via --port)

Endpoints:
  GET  /                  — Web UI form + dashboard
  POST /api/submit        — Submit a source URL  {"match": "...", "url": "..."}
  GET  /api/matches       — List active/upcoming matches with sources
  GET  /api/health        — Health check
"""
import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCHEDULE_FILE = "data/match_schedule.json"
HOT_LOG_FILE = "data/hoturl_log.json"
MAX_SOURCES_PER_MATCH = 16
AUTH_TOKEN = os.environ.get("HOTURL_TOKEN", "")  # Optional auth token

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Hot URL API", docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_schedule():
    if not os.path.exists(SCHEDULE_FILE):
        return []
    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_schedule(schedule):
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2)


def log_submission(entry):
    logs = []
    if os.path.exists(HOT_LOG_FILE):
        try:
            with open(HOT_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []
    logs.append(entry)
    # Keep last 500 entries
    if len(logs) > 500:
        logs = logs[-500:]
    with open(HOT_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2)


def canonical_url(url):
    url = str(url or "").strip().rstrip("/").lower()
    url = re.sub(r"^https?://(?:www\.)?", "", url)
    return url


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def match_name_matches(schedule_name, query):
    """Fuzzy match: check if query tokens are in the schedule name."""
    query_tokens = set(re.findall(r"[a-z]+", query.lower()))
    name_tokens = set(re.findall(r"[a-z]+", schedule_name.lower()))
    # Remove noise words
    noise = {"vs", "v", "and", "the", "fifa", "world", "cup", "2026", "live", "stream"}
    query_tokens -= noise
    name_tokens -= noise
    if not query_tokens:
        return False
    return query_tokens.issubset(name_tokens)


def find_match(schedule, identifier):
    """Find match by name, slug, fixture_id, or fuzzy match."""
    identifier = str(identifier).strip()
    slug = slugify(identifier)
    
    # Exact match on name, fixture_id, or match_key
    for m in schedule:
        if m.get("status") == "completed":
            continue
        if slugify(m.get("match_name", "")) == slug:
            return m
        if m.get("fixture_id", "") == identifier:
            return m
        if m.get("match_key", "") == identifier:
            return m

    # Fuzzy match on name
    for m in schedule:
        if m.get("status") == "completed":
            continue
        if match_name_matches(m.get("match_name", ""), identifier):
            return m

    return None


def source_urls_for_match(match):
    raw = match.get("source_url", [])
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, list):
        return [str(u).strip() for u in raw if str(u).strip()]
    return []


def inject_source_url(match, url):
    """Add a source URL to a match. Returns (success, message)."""
    url = str(url).strip()
    if not url.startswith(("http://", "https://")):
        return False, "URL must start with http:// or https://"

    existing = source_urls_for_match(match)
    canon_new = canonical_url(url)
    for existing_url in existing:
        if canonical_url(existing_url) == canon_new:
            return False, f"URL already exists for {match['match_name']}"

    if len(existing) >= MAX_SOURCES_PER_MATCH:
        return False, f"Max sources ({MAX_SOURCES_PER_MATCH}) reached for {match['match_name']}"

    existing.append(url)
    match["source_url"] = existing if len(existing) > 1 else existing[0]

    # Clear last_run_time so the scheduler processes this match immediately
    # on the next cron pass instead of waiting for cooldown to expire.
    match.pop("last_run_time", None)

    # Add source record
    records = match.get("source_records") if isinstance(match.get("source_records"), list) else []
    records.append({
        "url": url,
        "domain": re.sub(r"^www\.", "", (url.split("/")[2] if len(url.split("/")) > 2 else "")),
        "score": 5,  # High priority for manually submitted
        "discovered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_success_at": "",
        "fail_count": 0,
        "source": "hoturl",
    })
    match["source_records"] = records

    return True, f"Added to {match['match_name']} ({len(existing)} total sources)"


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
class SubmitRequest(BaseModel):
    match: str
    url: str
    token: str = ""


@app.post("/api/submit")
async def submit_url(req: SubmitRequest):
    if AUTH_TOKEN and req.token != AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")

    schedule = load_schedule()
    match = find_match(schedule, req.match)
    if not match:
        available = [m["match_name"] for m in schedule if m.get("status") not in ("completed",)]
        raise HTTPException(
            status_code=404,
            detail=f"Match not found: '{req.match}'. Available: {', '.join(available[:10])}"
        )

    success, message = inject_source_url(match, req.url)
    if not success:
        raise HTTPException(status_code=409, detail=message)

    save_schedule(schedule)
    log_submission({
        "match": match["match_name"],
        "url": req.url,
        "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_count": len(source_urls_for_match(match)),
    })

    # Trigger an immediate scheduler run in the background so the new
    # source gets scraped within seconds instead of waiting for cron.
    try:
        subprocess.Popen(
            ["python3", "match_scheduler.py", "--once"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdout=open("data/scheduler.log", "a"),
            stderr=subprocess.STDOUT,
        )
        triggered = True
    except Exception:
        triggered = False

    return {
        "status": "ok",
        "message": message,
        "match": match["match_name"],
        "instant_scrape": triggered,
    }


@app.get("/api/matches")
async def list_matches():
    schedule = load_schedule()
    now = datetime.now(timezone.utc)
    matches = []
    for m in schedule:
        if m.get("status") == "completed":
            continue
        try:
            mt = datetime.fromisoformat(m["match_time"].replace("Z", "+00:00"))
        except Exception:
            continue
        hours_away = (mt - now).total_seconds() / 3600
        sources = source_urls_for_match(m)
        matches.append({
            "match_name": m.get("match_name"),
            "match_time": m.get("match_time"),
            "kickoff_ist": mt.astimezone(IST).strftime("%b %d, %H:%M IST"),
            "hours_away": round(hours_away, 1),
            "status": m.get("status", "pending"),
            "source_count": len(sources),
            "sources": sources,
            "has_page": bool(m.get("new_blogger_page_id")),
            "has_post": bool(m.get("new_blogger_post_id")),
            "live": bool(m.get("new_blog_iframe_set")),
        })
    matches.sort(key=lambda x: x["hours_away"])
    return {"matches": matches, "count": len(matches)}


@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    schedule = load_schedule()
    now = datetime.now(timezone.utc)

    match_rows = []
    for m in schedule:
        if m.get("status") == "completed":
            continue
        try:
            mt = datetime.fromisoformat(m["match_time"].replace("Z", "+00:00"))
        except Exception:
            continue
        hours_away = (mt - now).total_seconds() / 3600
        sources = source_urls_for_match(m)
        status = m.get("status", "pending")
        live = m.get("new_blog_iframe_set", False)

        if status == "ended":
            badge = '<span class="badge ended">ENDED</span>'
        elif live:
            badge = '<span class="badge live">LIVE</span>'
        elif hours_away <= 0.5:
            badge = '<span class="badge active">ACTIVE</span>'
        elif hours_away <= 24:
            badge = '<span class="badge soon">SOON</span>'
        else:
            badge = '<span class="badge pending">UPCOMING</span>'

        source_items = "".join(
            f'<div class="src-item"><span class="src-domain">{u.split("/")[2] if len(u.split("/")) > 2 else "?"}</span>'
            f'<a href="{u}" target="_blank" class="src-url">{u[:80]}{"..." if len(u) > 80 else ""}</a></div>'
            for u in sources
        )
        if not source_items:
            source_items = '<div class="src-empty">No sources yet</div>'

        match_rows.append({
            "name": m.get("match_name", "?"),
            "kickoff": mt.astimezone(IST).strftime("%b %d, %H:%M"),
            "hours": hours_away,
            "badge": badge,
            "src_count": len(sources),
            "source_items": source_items,
            "slug": slugify(m.get("match_name", "")),
        })

    match_rows.sort(key=lambda x: x["hours"])

    rows_html = ""
    for r in match_rows:
        rows_html += f'''
        <div class="match-card" id="match-{r['slug']}">
          <div class="match-header">
            <div class="match-info">
              <div class="match-name">{r['name']}</div>
              <div class="match-meta">{r['kickoff']} IST · {r['src_count']} source(s)</div>
            </div>
            {r['badge']}
          </div>
          <div class="sources">{r['source_items']}</div>
        </div>'''

    options_html = "".join(
        f'<option value="{r["name"]}">{r["name"]} — {r["kickoff"]} IST</option>'
        for r in match_rows
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🔥 Hot URL — Source Injector</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Inter', -apple-system, sans-serif;
    background: #0f1117;
    color: #e4e4e7;
    min-height: 100vh;
  }}
  .container {{ max-width: 800px; margin: 0 auto; padding: 20px 16px; }}
  
  h1 {{
    font-size: 24px; font-weight: 700; margin-bottom: 4px;
    background: linear-gradient(135deg, #f97316, #ef4444);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }}
  .subtitle {{ color: #71717a; font-size: 13px; margin-bottom: 24px; }}

  /* Submit Form */
  .form-card {{
    background: #1a1b23; border: 1px solid #27272a;
    border-radius: 12px; padding: 20px; margin-bottom: 28px;
  }}
  .form-card h2 {{ font-size: 16px; font-weight: 600; margin-bottom: 14px; color: #f97316; }}
  .form-row {{ margin-bottom: 12px; }}
  .form-row label {{ display: block; font-size: 12px; font-weight: 500; color: #a1a1aa; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .form-row select, .form-row input {{
    width: 100%; padding: 10px 12px; background: #0f1117; border: 1px solid #3f3f46;
    border-radius: 8px; color: #e4e4e7; font-size: 14px; font-family: inherit;
    outline: none; transition: border-color 0.2s;
  }}
  .form-row select:focus, .form-row input:focus {{ border-color: #f97316; }}
  .btn-submit {{
    width: 100%; padding: 12px; background: linear-gradient(135deg, #f97316, #ea580c);
    color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600;
    cursor: pointer; transition: all 0.2s; font-family: inherit;
  }}
  .btn-submit:hover {{ opacity: 0.9; transform: translateY(-1px); }}
  .btn-submit:disabled {{ opacity: 0.5; cursor: not-allowed; transform: none; }}

  #result {{
    margin-top: 10px; padding: 10px 14px; border-radius: 8px; font-size: 13px; display: none;
  }}
  #result.success {{ display: block; background: #052e16; border: 1px solid #166534; color: #4ade80; }}
  #result.error {{ display: block; background: #2a0a0a; border: 1px solid #991b1b; color: #fca5a5; }}

  /* Match Cards */
  .section-title {{ font-size: 14px; font-weight: 600; color: #71717a; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }}
  .match-card {{
    background: #1a1b23; border: 1px solid #27272a; border-radius: 12px;
    padding: 16px; margin-bottom: 10px; transition: border-color 0.2s;
  }}
  .match-card:hover {{ border-color: #3f3f46; }}
  .match-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
  .match-name {{ font-size: 15px; font-weight: 600; }}
  .match-meta {{ font-size: 12px; color: #71717a; margin-top: 2px; }}
  
  .badge {{
    font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 20px;
    text-transform: uppercase; letter-spacing: 0.5px;
  }}
  .badge.live {{ background: #052e16; color: #4ade80; border: 1px solid #166534; }}
  .badge.active {{ background: #1e1b4b; color: #818cf8; border: 1px solid #3730a3; }}
  .badge.soon {{ background: #422006; color: #fb923c; border: 1px solid #92400e; }}
  .badge.pending {{ background: #1c1917; color: #78716c; border: 1px solid #44403c; }}
  .badge.ended {{ background: #1c1917; color: #a8a29e; border: 1px solid #44403c; }}

  .sources {{ margin-top: 6px; }}
  .src-item {{
    display: flex; align-items: center; gap: 8px; padding: 4px 0;
    font-size: 12px; border-bottom: 1px solid #1f1f23;
  }}
  .src-item:last-child {{ border-bottom: none; }}
  .src-domain {{
    background: #27272a; color: #a1a1aa; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 500; white-space: nowrap; min-width: 120px; text-align: center;
  }}
  .src-url {{ color: #60a5fa; text-decoration: none; word-break: break-all; }}
  .src-url:hover {{ color: #93c5fd; }}
  .src-empty {{ color: #52525b; font-size: 12px; font-style: italic; padding: 4px 0; }}
</style>
</head>
<body>
<div class="container">
  <h1>🔥 Hot URL Injector</h1>
  <p class="subtitle">Submit stream source URLs for upcoming matches. Instantly triggers a scrape after injection.</p>

  <div class="form-card">
    <h2>Submit Source URL</h2>
    <form id="submitForm" onsubmit="return submitUrl(event)">
      <div class="form-row">
        <label>Match</label>
        <select id="matchSelect" required>
          <option value="">— Select match —</option>
          {options_html}
        </select>
      </div>
      <div class="form-row">
        <label>Source URL</label>
        <input type="url" id="urlInput" placeholder="https://example.com/match-stream-page" required>
      </div>
      <button type="submit" class="btn-submit" id="btnSubmit">🚀 Inject Source URL</button>
    </form>
    <div id="result"></div>
  </div>

  <div class="section-title">Matches & Sources</div>
  {rows_html}
</div>

<script>
async function submitUrl(e) {{
  e.preventDefault();
  const btn = document.getElementById('btnSubmit');
  const result = document.getElementById('result');
  const matchVal = document.getElementById('matchSelect').value;
  const urlVal = document.getElementById('urlInput').value.trim();
  
  btn.disabled = true;
  btn.textContent = 'Submitting...';
  result.className = '';
  result.style.display = 'none';

  try {{
    const resp = await fetch('/api/submit', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ match: matchVal, url: urlVal, token: '' }})
    }});
    const data = await resp.json();
    if (resp.ok) {{
      result.className = 'success';
      result.textContent = '✅ ' + data.message;
      document.getElementById('urlInput').value = '';
      setTimeout(() => location.reload(), 1500);
    }} else {{
      result.className = 'error';
      result.textContent = '❌ ' + (data.detail || 'Unknown error');
    }}
  }} catch (err) {{
    result.className = 'error';
    result.textContent = '❌ Network error: ' + err.message;
  }}
  
  btn.disabled = false;
  btn.textContent = '🚀 Inject Source URL';
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hot URL API Server")
    parser.add_argument("--port", type=int, default=8899, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()

    print(f"[*] Hot URL API starting on http://{args.host}:{args.port}")
    print(f"[*] Dashboard: http://localhost:{args.port}/")
    print(f"[*] API docs:  http://localhost:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
