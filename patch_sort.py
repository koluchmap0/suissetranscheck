"""Patch: Trier MES SMTP par categorie (SPOOF / BASIQUE / AUTRE) au lieu de par provider."""

with open("auditguard/sender_bot.py", "r") as f:
    content = f.read()

# ============================================================
# 1. Replace _kb_smtp_list to group by category
# ============================================================
old_kb = '''def _kb_smtp_list(smtp_list: list[dict]) -> InlineKeyboardMarkup:
    """Build keyboard with all SMTP credentials as buttons."""
    buttons = []
    # Group by provider
    by_provider: dict[str, list[dict]] = {}
    for s in smtp_list:
        prov = (s.get("provider") or "smtp").upper()
        if prov not in by_provider:
            by_provider[prov] = []
        by_provider[prov].append(s)
    
    # Create buttons: AWS 1, AWS 2, Brevo 1, etc.
    for prov, creds in sorted(by_provider.items()):
        row = []
        for i, cred in enumerate(creds, 1):
            label = f"{prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"smtp_view_{cred['id']}"))
            if len(row) == 3:  # Max 3 per row
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
    
    buttons.append([
        InlineKeyboardButton("ADD SMTP", callback_data="add_smtp"),
        InlineKeyboardButton("Retour", callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(buttons)'''

new_kb = '''def _classify_smtp(s: dict) -> str:
    """Classify SMTP as SPOOF, BASIQUE, or AUTRE based on context."""
    ctx = (s.get("context") or "")
    if "SPOOF_" in ctx and "BASIQUE" not in ctx:
        return "SPOOF"
    elif "BASIQUE_" in ctx:
        return "BASIQUE"
    return "AUTRE"


def _kb_smtp_list(smtp_list: list[dict]) -> InlineKeyboardMarkup:
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

if old_kb in content:
    content = content.replace(old_kb, new_kb, 1)
    print("OK - _kb_smtp_list replaced")
else:
    print("WARN - _kb_smtp_list not found exactly, trying line-by-line")
    # Fallback: find by function name
    import re
    pattern = r'def _kb_smtp_list\(smtp_list.*?\n    return InlineKeyboardMarkup\(buttons\)'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        content = content[:match.start()] + new_kb + content[match.end():]
        print("OK - replaced via regex")
    else:
        print("ERROR - could not find _kb_smtp_list")

# ============================================================
# 2. Add noop handler (for category header buttons)
# ============================================================
# Find the callback handler and add noop at the start
old_noop_check = '    data = query.data\n    chat_id = query.message.chat_id'
new_noop_check = '''    data = query.data
    chat_id = query.message.chat_id

    # Ignore noop buttons (category headers)
    if data == "noop":
        return'''

if old_noop_check in content:
    content = content.replace(old_noop_check, new_noop_check, 1)
    print("OK - noop handler added")
else:
    print("WARN - noop insertion point not found")

with open("auditguard/sender_bot.py", "w") as f:
    f.write(content)

print("PATCH COMPLETE")
