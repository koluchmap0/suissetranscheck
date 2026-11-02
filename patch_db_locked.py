#!/usr/bin/env python3
"""
Fix database locked errors across all files.
Changes:
1. Increase ALL sqlite3 timeouts to 120s
2. Add WAL mode + busy_timeout PRAGMA to all connections
3. Add retry wrapper for DB writes
4. Fix campaign_sender to use WAL properly
"""

import re

# ============================================================
# FIX 1: sender_bot.py — increase all timeouts, add WAL
# ============================================================
BOT_FILE = "/opt/darkvasend/auditguard/sender_bot.py"
with open(BOT_FILE, "r") as f:
    bot_content = f.read()

# Replace all timeout=10 with timeout=120 for sqlite3.connect
bot_content = bot_content.replace("timeout=10)", "timeout=120)")
bot_content = bot_content.replace("timeout=5)", "timeout=120)")

# Add WAL mode + busy_timeout after each sqlite3.connect that doesn't have it
# Pattern: _db = _sql.connect(DB_PATH, timeout=120)
# Should be followed by WAL pragma

# Fix _ensure_db_schema
old_schema = '''    _db = _sql.connect(DB_PATH, timeout=120)
    _db.executescript("""'''
new_schema = '''    _db = _sql.connect(DB_PATH, timeout=120)
    _db.execute("PRAGMA journal_mode=WAL")
    _db.execute("PRAGMA busy_timeout=120000")
    _db.executescript("""'''
bot_content = bot_content.replace(old_schema, new_schema)

# Fix _add_manual_smtp
old_add = '''    db = sqlite3.connect(DB_PATH, timeout=120)
    db.row_factory = sqlite3.Row
    # Ensure table exists'''
new_add = '''    db = sqlite3.connect(DB_PATH, timeout=120)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=120000")
    # Ensure table exists'''
bot_content = bot_content.replace(old_add, new_add)

# Fix _get_all_smtp
old_get = '''        db = sqlite3.connect(DB_PATH, timeout=120)
        db.row_factory = sqlite3.Row
        # Ensure table exists
        db.execute("""CREATE TABLE IF NOT EXISTS found_creds'''
new_get = '''        db = sqlite3.connect(DB_PATH, timeout=120)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=120000")
        # Ensure table exists
        db.execute("""CREATE TABLE IF NOT EXISTS found_creds'''
bot_content = bot_content.replace(old_get, new_get)

# Fix _get_smtp_by_id
old_byid = '''        db = sqlite3.connect(DB_PATH, timeout=120)
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM found_creds'''
new_byid = '''        db = sqlite3.connect(DB_PATH, timeout=120)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=120000")
        row = db.execute("SELECT * FROM found_creds'''
bot_content = bot_content.replace(old_byid, new_byid)

# Fix _get_smtp_provider_affinity db connection
old_affinity = '''            _db2 = _sql2.connect(db_path, timeout=120)
            row = _db2.execute'''
new_affinity = '''            _db2 = _sql2.connect(db_path, timeout=120)
            _db2.execute("PRAGMA journal_mode=WAL")
            _db2.execute("PRAGMA busy_timeout=120000")
            row = _db2.execute'''
bot_content = bot_content.replace(old_affinity, new_affinity)

# Fix the campaign mode filter db connection
old_camp_filter = '''        _db = _sql.connect(db_path, timeout=120)
        _db.row_factory = _sql.Row
        _spoof_inbox_ids = set()'''
new_camp_filter = '''        _db = _sql.connect(db_path, timeout=120)
        _db.row_factory = _sql.Row
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("PRAGMA busy_timeout=120000")
        _spoof_inbox_ids = set()'''
bot_content = bot_content.replace(old_camp_filter, new_camp_filter)

# Fix the health check db connection
old_hc_db = '''            db = _sql.connect(DB_PATH, timeout=120)
            db.row_factory = _sql.Row
            rows = db.execute('''
new_hc_db = '''            db = _sql.connect(DB_PATH, timeout=120)
            db.row_factory = _sql.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=120000")
            rows = db.execute('''
bot_content = bot_content.replace(old_hc_db, new_hc_db)

# Add retry logic to _add_manual_smtp — wrap the INSERT in a retry loop
old_insert = '''    with db:
        cur = db.execute(
            """INSERT INTO found_creds
               (domain, provider, cred_type, value, source_url, source_type, context,
                smtp_host, smtp_port, smtp_user, smtp_password,
                found_at, exploited, auth_ok, email_sent, notified)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,1,0,0)""",
            (domain, provider, "password", password, "manual_add", "manual",
             f"Manual: {label}", host, port, user, password, time.time()),
        )
        cred_id = cur.lastrowid
    db.close()
    return cred_id'''
new_insert = '''    for _retry in range(5):
        try:
            with db:
                cur = db.execute(
                    """INSERT INTO found_creds
                       (domain, provider, cred_type, value, source_url, source_type, context,
                        smtp_host, smtp_port, smtp_user, smtp_password,
                        found_at, exploited, auth_ok, email_sent, notified)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,1,0,0)""",
                    (domain, provider, "password", password, "manual_add", "manual",
                     f"Manual: {label}", host, port, user, password, time.time()),
                )
                cred_id = cur.lastrowid
            db.close()
            return cred_id
        except Exception as _e:
            if "locked" in str(_e).lower() and _retry < 4:
                time.sleep(0.5 * (2 ** _retry))
                continue
            db.close()
            raise'''
bot_content = bot_content.replace(old_insert, new_insert)

# Wrap query.answer() in try/except to handle stale callbacks
old_answer = '''    await query.answer()
    if not _allowed(update):
        return await query.edit_message_text("Acces refuse.")'''
new_answer = '''    try:
        await query.answer()
    except Exception:
        pass  # Stale callback, continue anyway
    if not _allowed(update):
        return await query.edit_message_text("Acces refuse.")'''
bot_content = bot_content.replace(old_answer, new_answer)

with open(BOT_FILE, "w") as f:
    f.write(bot_content)
print(f"sender_bot.py fixed ({len(bot_content)} bytes)")


# ============================================================
# FIX 2: campaign_sender.py — increase timeout, add busy_timeout
# ============================================================
CAMP_FILE = "/opt/darkvasend/auditguard/campaign_sender.py"
with open(CAMP_FILE, "r") as f:
    camp_content = f.read()

# Add busy_timeout pragma to CampaignManager.__init__
old_camp_init = '''        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(CAMPAIGN_SCHEMA)'''
new_camp_init = '''        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=120000")
        self.db.executescript(CAMPAIGN_SCHEMA)'''
camp_content = camp_content.replace(old_camp_init, new_camp_init)

# Add retry logic to create_campaign
old_create = '''    def create_campaign(
        self,
        name: str,
        subject: str,
        letter_html: str,
        from_name: str = "",
        from_email: str = "",
        reply_to: str = "",
        letter_text: str = "",
        rate_per_minute: int = 30,
    ) -> int:
        """Create a new campaign. Returns campaign ID."""
        with self.db:
            cur = self.db.execute(
                """INSERT INTO campaigns
                   (name, subject, from_name, from_email, reply_to, letter_html, letter_text,
                    status, created_at, rate_per_minute)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (name, subject, from_name, from_email, reply_to, letter_html, letter_text,
                 time.time(), rate_per_minute),
            )
            return cur.lastrowid'''
new_create = '''    def create_campaign(
        self,
        name: str,
        subject: str,
        letter_html: str,
        from_name: str = "",
        from_email: str = "",
        reply_to: str = "",
        letter_text: str = "",
        rate_per_minute: int = 30,
    ) -> int:
        """Create a new campaign. Returns campaign ID."""
        for _retry in range(5):
            try:
                with self.db:
                    cur = self.db.execute(
                        """INSERT INTO campaigns
                           (name, subject, from_name, from_email, reply_to, letter_html, letter_text,
                            status, created_at, rate_per_minute)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                        (name, subject, from_name, from_email, reply_to, letter_html, letter_text,
                         time.time(), rate_per_minute),
                    )
                    return cur.lastrowid
            except Exception as e:
                if "locked" in str(e).lower() and _retry < 4:
                    time.sleep(0.5 * (2 ** _retry))
                    continue
                raise'''
camp_content = camp_content.replace(old_create, new_create)

# Add retry to claim_recipient (called heavily during campaigns)
old_claim = '''    def claim_recipient(self, campaign_id: int) -> dict | None:
        """Claim next pending recipient."""
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
        return None'''
new_claim = '''    def claim_recipient(self, campaign_id: int) -> dict | None:
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
camp_content = camp_content.replace(old_claim, new_claim)

# Add retry to mark_sent and mark_failed
old_mark_sent = '''    def mark_sent(self, recipient_id: int, smtp_info: str):
        with self.db:
            self.db.execute(
                "UPDATE campaign_recipients SET status = 'sent', sent_at = ?, smtp_used = ? WHERE id = ?",
                (time.time(), smtp_info, recipient_id),
            )
            # Get campaign_id for count update
            row = self.db.execute("SELECT campaign_id FROM campaign_recipients WHERE id = ?", (recipient_id,)).fetchone()
            if row:
                self.db.execute(
                    "UPDATE campaigns SET sent_count = sent_count + 1 WHERE id = ?",
                    (row["campaign_id"],),
                )'''
new_mark_sent = '''    def mark_sent(self, recipient_id: int, smtp_info: str):
        for _retry in range(5):
            try:
                with self.db:
                    self.db.execute(
                        "UPDATE campaign_recipients SET status = 'sent', sent_at = ?, smtp_used = ? WHERE id = ?",
                        (time.time(), smtp_info, recipient_id),
                    )
                    row = self.db.execute("SELECT campaign_id FROM campaign_recipients WHERE id = ?", (recipient_id,)).fetchone()
                    if row:
                        self.db.execute(
                            "UPDATE campaigns SET sent_count = sent_count + 1 WHERE id = ?",
                            (row["campaign_id"],),
                        )
                return
            except Exception as e:
                if "locked" in str(e).lower() and _retry < 4:
                    time.sleep(0.3 * (2 ** _retry))
                    continue
                raise'''
camp_content = camp_content.replace(old_mark_sent, new_mark_sent)

old_mark_failed = '''    def mark_failed(self, recipient_id: int, error: str, smtp_info: str = ""):
        with self.db:
            self.db.execute(
                "UPDATE campaign_recipients SET status = 'failed', error = ?, smtp_used = ? WHERE id = ?",
                (error[:500], smtp_info, recipient_id),
            )
            row = self.db.execute("SELECT campaign_id FROM campaign_recipients WHERE id = ?", (recipient_id,)).fetchone()
            if row:
                self.db.execute(
                    "UPDATE campaigns SET failed_count = failed_count + 1 WHERE id = ?",
                    (row["campaign_id"],),
                )'''
new_mark_failed = '''    def mark_failed(self, recipient_id: int, error: str, smtp_info: str = ""):
        for _retry in range(5):
            try:
                with self.db:
                    self.db.execute(
                        "UPDATE campaign_recipients SET status = 'failed', error = ?, smtp_used = ? WHERE id = ?",
                        (error[:500], smtp_info, recipient_id),
                    )
                    row = self.db.execute("SELECT campaign_id FROM campaign_recipients WHERE id = ?", (recipient_id,)).fetchone()
                    if row:
                        self.db.execute(
                            "UPDATE campaigns SET failed_count = failed_count + 1 WHERE id = ?",
                            (row["campaign_id"],),
                        )
                return
            except Exception as e:
                if "locked" in str(e).lower() and _retry < 4:
                    time.sleep(0.3 * (2 ** _retry))
                    continue
                raise'''
camp_content = camp_content.replace(old_mark_failed, new_mark_failed)

with open(CAMP_FILE, "w") as f:
    f.write(camp_content)
print(f"campaign_sender.py fixed ({len(camp_content)} bytes)")


# ============================================================
# FIX 3: db.py — increase timeout, add WAL + busy_timeout
# ============================================================
DB_FILE = "/opt/darkvasend/auditguard/db.py"
with open(DB_FILE, "r") as f:
    db_content = f.read()

old_db_init = '''        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")'''
new_db_init = '''        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=120000")'''
db_content = db_content.replace(old_db_init, new_db_init)

with open(DB_FILE, "w") as f:
    f.write(db_content)
print(f"db.py fixed ({len(db_content)} bytes)")

print("\nAll database lock fixes applied!")
