#!/usr/bin/env python3
"""
Patch: Fix SQLite locking issues in campaign_sender.py and sender_bot.py
- Increase busy_timeout to 300s
- claim_recipient: 30 retries with longer waits
- Add reconnect logic on persistent lock
- Wrap campaign loop in try/except to prevent silent death
- Reset orphan 'sending' recipients on campaign start
"""
import re

# ============================================================
# PATCH 1: campaign_sender.py — better DB resilience
# ============================================================
CS_PATH = "/opt/darkvasend/auditguard/campaign_sender.py"

with open(CS_PATH, "r") as f:
    cs = f.read()

# 1a. Increase connection timeout from 30 to 120, busy_timeout from 120000 to 300000
cs = cs.replace(
    'self.db = sqlite3.connect(db_path, timeout=30, check_same_thread=False)',
    'self.db = sqlite3.connect(db_path, timeout=120, check_same_thread=False)'
)
cs = cs.replace(
    'self.db.execute("PRAGMA busy_timeout=120000")',
    'self.db.execute("PRAGMA busy_timeout=300000")'
)

# 1b. Replace claim_recipient with more robust version
old_claim = '''    def claim_recipient(self, campaign_id: int) -> dict | None:
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

new_claim = '''    def claim_recipient(self, campaign_id: int) -> dict | None:
        """Claim next pending recipient. Ultra-resilient DB lock handling."""
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
                err_str = str(e).lower()
                if ("locked" in err_str or "busy" in err_str) and _retry < 29:
                    wait = min(1.0 + 0.5 * _retry, 15.0)
                    time.sleep(wait)
                    # Reconnect DB on persistent lock (every 10 retries)
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

cs = cs.replace(old_claim, new_claim)

# 1c. Store db_path for reconnect
cs = cs.replace(
    '''    def __init__(self, db_path: str = DB_PATH):
        self.db = sqlite3.connect(db_path, timeout=120, check_same_thread=False)''',
    '''    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self.db = sqlite3.connect(db_path, timeout=120, check_same_thread=False)'''
)

# 1d. Add reset_orphan_sending method
reset_method = '''
    def reset_orphan_sending(self, campaign_id: int) -> int:
        """Reset any 'sending' recipients back to 'pending' (crash recovery)."""
        for _retry in range(5):
            try:
                with self.db:
                    cur = self.db.execute(
                        "UPDATE campaign_recipients SET status = 'pending' WHERE campaign_id = ? AND status = 'sending'",
                        (campaign_id,),
                    )
                    return cur.rowcount
            except Exception as e:
                if "locked" in str(e).lower() and _retry < 4:
                    time.sleep(1.0 * (2 ** _retry))
                    continue
                raise
        return 0

'''

# Insert after close() method
cs = cs.replace(
    '''    def close(self):
        self.db.close()''',
    '''    def close(self):
        self.db.close()
''' + reset_method
)

# Also fix mark_sent and mark_failed with more retries
cs = cs.replace(
    '''    def mark_sent(self, recipient_id: int, smtp_info: str):
        for _retry in range(5):''',
    '''    def mark_sent(self, recipient_id: int, smtp_info: str):
        for _retry in range(15):'''
)
cs = cs.replace(
    '''    def mark_failed(self, recipient_id: int, error: str, smtp_info: str = ""):
        for _retry in range(5):''',
    '''    def mark_failed(self, recipient_id: int, error: str, smtp_info: str = ""):
        for _retry in range(15):'''
)

# Fix retry bounds in mark_sent/mark_failed
cs = cs.replace('if "locked" in str(e).lower() and _retry < 4:\n                    time.sleep(0.3 * (2 ** _retry))',
                'if "locked" in str(e).lower() and _retry < 14:\n                    time.sleep(min(0.5 * (2 ** min(_retry, 5)), 15.0))')

with open(CS_PATH, "w") as f:
    f.write(cs)

print("campaign_sender.py PATCHED")

# ============================================================
# PATCH 2: sender_bot.py — wrap campaign loop in try/except
# ============================================================
SB_PATH = "/opt/darkvasend/auditguard/sender_bot.py"

with open(SB_PATH, "r") as f:
    sb = f.read()

# 2a. Fix the main campaign loop — add try/except around the while True
# Find the pattern and replace with protected version

old_try_block = '''    try:
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

new_try_block = '''    # Reset orphan sending recipients from previous crash
    try:
        reset_count = mgr.reset_orphan_sending(campaign_id)
        if reset_count:
            print(f"[CAMP] Reset {reset_count} orphan sending -> pending", flush=True)
    except Exception as _e:
        print(f"[CAMP] reset_orphan warning: {_e}", flush=True)

    _consecutive_db_errors = 0
    _MAX_DB_ERRORS = 50  # Allow many retries before giving up

    try:
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
                    db_retries = 0
                    _consecutive_db_errors = 0  # Reset on success
                except Exception as _db_err:
                    if "locked" in str(_db_err).lower() and db_retries < 20:
                        db_retries += 1
                        _consecutive_db_errors += 1
                        print(f"[CAMP] DB locked on claim, retry {db_retries} (total: {_consecutive_db_errors})...", flush=True)
                        # Reconnect DB after many consecutive errors
                        if _consecutive_db_errors > 0 and _consecutive_db_errors % 10 == 0:
                            print(f"[CAMP] Reconnecting DB after {_consecutive_db_errors} errors...", flush=True)
                            try:
                                mgr.close()
                            except Exception:
                                pass
                            mgr = CampaignManager(db_path)
                        await asyncio.sleep(min(2 + db_retries, 30))
                        continue
                    else:
                        print(f"[CAMP] claim_recipient error: {_db_err}", flush=True)
                        _consecutive_db_errors += 1
                        break

            # Safety: if too many consecutive DB errors, wait longer then retry
            if _consecutive_db_errors >= _MAX_DB_ERRORS:
                print(f"[CAMP] {_consecutive_db_errors} consecutive DB errors — reconnecting and waiting 60s...", flush=True)
                try:
                    mgr.close()
                except Exception:
                    pass
                mgr = CampaignManager(db_path)
                await asyncio.sleep(60)
                _consecutive_db_errors = 0
                continue

            if not batch:
                try:
                    stats_check = mgr.get_stats(campaign_id)
                    if stats_check.pending == 0:
                        break
                    print(f"[CAMP] No batch but {stats_check.pending} pending — waiting 10s...", flush=True)
                    await asyncio.sleep(10)
                    continue
                except Exception:
                    # DB error on stats — reconnect and retry
                    try:
                        mgr.close()
                    except Exception:
                        pass
                    mgr = CampaignManager(db_path)
                    await asyncio.sleep(10)
                    continue'''

sb = sb.replace(old_try_block, new_try_block)

# 2b. Fix the finally block to ALWAYS update campaign status
old_finally = '''    finally:
        executor.shutdown(wait=False)

    status = "finished" if not mgr.claim_recipient(campaign_id) else "stopped"
    mgr.update_campaign_status(campaign_id, status)'''

new_finally = '''    except Exception as _fatal:
        print(f"[CAMP] FATAL ERROR in campaign #{campaign_id}: {_fatal}", flush=True)
        import traceback
        traceback.print_exc()
        send_telegram("CAMP #" + str(campaign_id) + " ERREUR FATALE: " + str(_fatal)[:200], chat_id)
    finally:
        try:
            executor.shutdown(wait=False)
        except Exception:
            pass

    try:
        status = "finished" if not mgr.claim_recipient(campaign_id) else "stopped"
    except Exception:
        status = "stopped"
    try:
        mgr.update_campaign_status(campaign_id, status)
    except Exception:
        pass'''

sb = sb.replace(old_finally, new_finally)

with open(SB_PATH, "w") as f:
    f.write(sb)

print("sender_bot.py PATCHED")
print("ALL PATCHES APPLIED")
