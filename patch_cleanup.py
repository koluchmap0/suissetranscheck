"""Patch: Remove orphaned code blocks after return statements."""

with open("auditguard/sender_bot.py", "r") as f:
    content = f.read()

# ============================================================
# 1. Remove orphaned template spoof result handling
# ============================================================
orphan_tpl = '''        return
            mode_used = result.get("spoof_mode_used", best_mode)
            dmarc_warn = ""
            if dmarc_policy == "reject":
                dmarc_warn = (
                    "\\nATTENTION: DMARC p=reject\\n"
                    "Gmail/Outlook vont probablement DROP ce mail.\\n"
                    "Teste avec un provider moins strict (Yahoo, petits FAI)."
                )
            elif dmarc_policy == "quarantine":
                dmarc_warn = "\\nDMARC p=quarantine — le mail peut aller en SPAM."
            msg = (
                "=============================\\n"
                f"  {tpl['brand']} SPOOF — ENVOYE !\\n"
                "=============================\\n\\n"
                f"Template: {tpl['brand']}\\n"
                f"From: {tpl['from_name']} <{tpl['from_email']}>\\n"
                f"To: {text}\\n"
                f"DMARC {dmarc_domain}: p={dmarc_policy}"
                f"{dmarc_warn}\\n\\n"
                "Verifie la boite mail !"
            )
        else:
            msg = (
                f"TEMPLATE SPOOF — ECHEC\\n\\n"
                f"Erreur: {result['error']}\\n"
                f"SMTP: {smtp['smtp_host']}:{smtp['smtp_port']}\\n\\n"
                "Essaie un autre SMTP ou template."
            )
        return await update.effective_message.reply_text(msg, reply_markup=_kb_main())'''

if orphan_tpl in content:
    content = content.replace(orphan_tpl, "        return", 1)
    print("OK - removed orphaned template spoof code")
else:
    print("WARN - orphaned template spoof not found exactly")

# ============================================================
# 2. Remove orphaned spoof test result handling
# ============================================================
orphan_spoof = '''        return
            mode_used = result.get("spoof_mode_used", best_mode)
            dmarc_warn = ""
            if dmarc_policy == "reject":
                dmarc_warn = (
                    "\\nATTENTION: DMARC p=reject\\n"
                    "Gmail/Outlook vont probablement DROP ce mail.\\n"
                    "Teste avec un provider moins strict."
                )
            elif dmarc_policy == "quarantine":
                dmarc_warn = "\\nDMARC p=quarantine — le mail peut aller en SPAM."
            text = (
                "=============================\\n"
                "  SPOOF TEST — ENVOYE !\\n"
                "=============================\\n\\n"
                f"From: {from_name} <{from_email}>\\n"
                f"To: {to_email}\\n"
                f"Sujet: {subject}\\n"
                f"DMARC {dmarc_domain}: p={dmarc_policy}"
                f"{dmarc_warn}\\n\\n"
                "Verifie la boite mail !"
            )
        else:
            text = (
                f"SPOOF TEST — ECHEC\\n\\n"
                f"Erreur: {result['error']}\\n"
                f"SMTP: {smtp['smtp_host']}:{smtp['smtp_port']}"
            )
        
        return await update.effective_message.reply_text(text, reply_markup=_kb_main())'''

if orphan_spoof in content:
    content = content.replace(orphan_spoof, "        return", 1)
    print("OK - removed orphaned spoof test code")
else:
    print("WARN - orphaned spoof test not found exactly")

with open("auditguard/sender_bot.py", "w") as f:
    f.write(content)

print("CLEANUP COMPLETE")
