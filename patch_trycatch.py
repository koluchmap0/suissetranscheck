"""Patch: Wrap reply_text in try/except for both spoof flows to handle Telegram API timeouts."""

with open("auditguard/sender_bot.py", "r") as f:
    content = f.read()

changes = 0

# ============================================================
# 1. Template spoof: wrap DMARC + reply_text in try/except
# ============================================================
old_tpl_reply = '''        # DMARC pre-check — auto-select best spoof mode
        from .campaign_sender import pick_best_spoof_mode
        best_mode, dmarc_info = await asyncio.to_thread(pick_best_spoof_mode, tpl["from_email"])
        dmarc_policy = dmarc_info.get("policy", "?")
        dmarc_domain = tpl["from_email"].split("@")[1] if "@" in tpl["from_email"] else "?"

        if dmarc_policy == "reject":
            mode_note = f"DMARC {dmarc_domain}: p=reject (Gmail/Outlook vont DROP)"
        elif dmarc_policy == "quarantine":
            mode_note = f"DMARC {dmarc_domain}: p=quarantine (risque SPAM)"
        else:
            mode_note = f"DMARC {dmarc_domain}: p={dmarc_policy} (spoof OK)"

        await update.effective_message.reply_text(
            f"ENVOI EN COURS...\\n"
            f"Template: {tpl['brand']}\\n"
            f"From: {tpl['from_name']} <{tpl['from_email']}>\\n"
            f"To: {text}\\n"
            f"{mode_note}"
        )'''

new_tpl_reply = '''        # DMARC pre-check — auto-select best spoof mode
        from .campaign_sender import pick_best_spoof_mode
        try:
            best_mode, dmarc_info = await asyncio.to_thread(pick_best_spoof_mode, tpl["from_email"])
        except Exception:
            best_mode, dmarc_info = "clean", {"policy": "unknown"}
        dmarc_policy = dmarc_info.get("policy", "?")
        dmarc_domain = tpl["from_email"].split("@")[1] if "@" in tpl["from_email"] else "?"

        try:
            await update.effective_message.reply_text(
                f"ENVOI EN COURS...\\n"
                f"Template: {tpl['brand']}\\n"
                f"From: {tpl['from_name']} <{tpl['from_email']}>\\n"
                f"To: {text}"
            )
        except Exception:
            pass  # Don't crash if Telegram API is slow'''

if old_tpl_reply in content:
    content = content.replace(old_tpl_reply, new_tpl_reply, 1)
    changes += 1
    print("OK - template spoof reply_text wrapped in try/except")
else:
    print("WARN - template spoof reply_text not found")

# ============================================================
# 2. Simple spoof test: find and wrap its DMARC + reply_text
# ============================================================
old_spoof_reply = '''        # DMARC pre-check
        from .campaign_sender import pick_best_spoof_mode
        best_mode, dmarc_info = await asyncio.to_thread(pick_best_spoof_mode, from_email)
        dmarc_policy = dmarc_info.get("policy", "?")
        dmarc_domain = from_email.split("@")[1] if "@" in from_email else "?"

        await update.effective_message.reply_text(
            f"ENVOI EN COURS...\\n"
            f"SMTP: {(smtp.get('provider') or '?').upper()} #{cred_id}\\n"
            f"From: {from_name} <{from_email}>\\n"
            f"To: {to_email}\\n"
            f"Sujet: {subject}\\n"
            f"DMARC {dmarc_domain}: p={dmarc_policy} -> {best_mode.upper()}"
        )'''

new_spoof_reply = '''        # DMARC pre-check
        from .campaign_sender import pick_best_spoof_mode
        try:
            best_mode, dmarc_info = await asyncio.to_thread(pick_best_spoof_mode, from_email)
        except Exception:
            best_mode, dmarc_info = "clean", {"policy": "unknown"}
        dmarc_policy = dmarc_info.get("policy", "?")
        dmarc_domain = from_email.split("@")[1] if "@" in from_email else "?"

        try:
            await update.effective_message.reply_text(
                f"ENVOI EN COURS...\\n"
                f"SMTP: {(smtp.get('provider') or '?').upper()} #{cred_id}\\n"
                f"From: {from_name} <{from_email}>\\n"
                f"To: {to_email}\\n"
                f"Sujet: {subject}"
            )
        except Exception:
            pass  # Don't crash if Telegram API is slow'''

if old_spoof_reply in content:
    content = content.replace(old_spoof_reply, new_spoof_reply, 1)
    changes += 1
    print("OK - spoof test reply_text wrapped in try/except")
else:
    print("WARN - spoof test reply_text not found")

print(f"CHANGES: {changes}")

with open("auditguard/sender_bot.py", "w") as f:
    f.write(content)

print("PATCH COMPLETE")
