"""Patch: Remove proxy from single spoof tests (direct only).
Proxy is for mass operations only. Single spoof tests should be direct + run in background."""

with open("auditguard/sender_bot.py", "r") as f:
    content = f.read()

changes = 0

# ============================================================
# 1. SPOOF TEST SIMPLE — remove proxy entirely, run in background
# ============================================================
old_spoof = '''        smtp_config = {
                "host": smtp["smtp_host"],
                "port": smtp["smtp_port"],
                "username": smtp["smtp_user"],
                "password": smtp["smtp_password"],
                "from_email": smtp.get("smtp_user", ""),
            }
        html_body = body if "<" in body else f"<p>{body}</p>"
        text_body = body if "<" not in body else ""

        result = await asyncio.to_thread(
            _send_one,
            smtp_config,
            from_name,
            from_email,
            to_email,
            subject,
            html_body,
            text_body,
            envelope_from=smtp.get("smtp_user", ""),
            spoof_mode=best_mode,
            proxy=_proxy2,
        )

        # If proxy error, retry WITHOUT proxy
        proxy_used_label = ""
        if not result["success"] and _proxy2 and "Proxy" in result.get("error", ""):
            await update.effective_message.reply_text(
                "Proxy erreur (" + result.get("error", "")[:60] + ")\\nRetry SANS proxy..."
            )
            result = await asyncio.to_thread(
                _send_one,
                smtp_config,
                from_name,
                from_email,
                to_email,
                subject,
                html_body,
                text_body,
                envelope_from=smtp.get("smtp_user", ""),
                spoof_mode=best_mode,
                proxy=None,
            )
            proxy_used_label = " (direct, sans proxy)"
        elif _proxy2:
            proxy_used_label = f" (via proxy)"

        if result["success"]:'''

new_spoof = '''        smtp_config = {
                "host": smtp["smtp_host"],
                "port": smtp["smtp_port"],
                "username": smtp["smtp_user"],
                "password": smtp["smtp_password"],
                "from_email": smtp.get("smtp_user", ""),
            }
        html_body = body if "<" in body else f"<p>{body}</p>"
        text_body = body if "<" not in body else ""

        # Launch spoof test in background to avoid Telegram timeout
        bot = update.get_bot()
        chat_id = update.effective_message.chat_id
        asyncio.create_task(
            _bg_spoof_test(bot, chat_id, smtp_config, from_name, from_email,
                           to_email, subject, html_body, text_body, best_mode,
                           dmarc_domain, dmarc_policy, cred_id)
        )
        return'''

if old_spoof in content:
    content = content.replace(old_spoof, new_spoof, 1)
    changes += 1
    print("OK - spoof test: removed proxy, added background task")
else:
    print("ERROR - spoof test block not found")

# Now remove the old success/failure handling that's now orphaned
# We need to find and remove everything from the old "if result["success"]:" up to the end of that flow
# Actually, we need to add the background function instead

# ============================================================
# 2. Add _bg_spoof_test background function (before run_sender)
# ============================================================
bg_func = '''
# ---------------------------------------------------------------------------
# Background spoof test — avoids Telegram handler timeout
# ---------------------------------------------------------------------------
async def _bg_spoof_test(bot, chat_id, smtp_config, from_name, from_email,
                         to_email, subject, html_body, text_body, spoof_mode,
                         dmarc_domain, dmarc_policy, cred_id):
    """Background spoof test — no proxy, direct connection."""
    from .campaign_sender import _send_one

    try:
        result = await asyncio.to_thread(
            _send_one,
            smtp_config,
            from_name,
            from_email,
            to_email,
            subject,
            html_body,
            text_body,
            envelope_from=smtp_config.get("from_email", ""),
            spoof_mode=spoof_mode,
            proxy=None,
        )

        if result["success"]:
            mode_used = result.get("spoof_mode_used", spoof_mode)
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
                f"Mode: {mode_used}\\n"
                f"DMARC {dmarc_domain}: p={dmarc_policy}"
                f"{dmarc_warn}\\n\\n"
                "Verifie la boite mail !"
            )
        else:
            text = (
                f"SPOOF TEST — ECHEC\\n\\n"
                f"Erreur: {result.get('error', 'inconnue')}\\n"
                f"SMTP: {smtp_config['host']}:{smtp_config['port']}\\n"
                f"Mode: {result.get('spoof_mode_used', spoof_mode)}"
            )

        await bot.send_message(chat_id=chat_id, text=text[:4000])
    except Exception as e:
        try:
            await bot.send_message(chat_id=chat_id,
                text=f"SPOOF TEST — ERREUR\\n{type(e).__name__}: {str(e)[:200]}")
        except Exception:
            pass


'''

# Insert before run_sender
if "def run_sender():" in content:
    content = content.replace("def run_sender():", bg_func + "def run_sender():", 1)
    changes += 1
    print("OK - added _bg_spoof_test function")
else:
    print("ERROR - run_sender not found")

# ============================================================
# 3. TEMPLATE SPOOF — same fix: remove proxy, run in background
# ============================================================
old_tpl = '''        from .campaign_sender import _send_one
        from .proxy_pool import ProxyPool
        _pool = ProxyPool(DB_PATH)
        _proxy = _pool.get_next() if _pool.count_active() > 0 else None
        _pool.close()

        _tpl_smtp_config = {
                "host": smtp["smtp_host"],
                "port": smtp["smtp_port"],
                "username": smtp["smtp_user"],
                "password": smtp["smtp_password"],
                "from_email": smtp.get("smtp_user", ""),
            }

        result = await asyncio.to_thread(
            _send_one,
            _tpl_smtp_config,
            tpl["from_name"],
            tpl["from_email"],
            text,
            tpl["subject"],
            tpl["html"],
            "",
            smtp.get("smtp_user", ""),
            tpl.get("reply_to", ""),
            best_mode,
            _proxy,
        )

        # If proxy error, retry WITHOUT proxy
        if not result["success"] and _proxy and "Proxy" in result.get("error", ""):
            await update.effective_message.reply_text(
                "Proxy erreur (" + result.get("error", "")[:60] + ")\\nRetry SANS proxy..."
            )
            result = await asyncio.to_thread(
                _send_one,
                _tpl_smtp_config,
                tpl["from_name"],
                tpl["from_email"],
                text,
                tpl["subject"],
                tpl["html"],
                "",
                smtp.get("smtp_user", ""),
                tpl.get("reply_to", ""),
                best_mode,
                None,
            )

        if result["success"]:'''

new_tpl = '''        _tpl_smtp_config = {
                "host": smtp["smtp_host"],
                "port": smtp["smtp_port"],
                "username": smtp["smtp_user"],
                "password": smtp["smtp_password"],
                "from_email": smtp.get("smtp_user", ""),
            }

        # Launch in background to avoid Telegram timeout
        bot = update.get_bot()
        chat_id = update.effective_message.chat_id
        asyncio.create_task(
            _bg_tpl_spoof_test(bot, chat_id, _tpl_smtp_config, tpl, text,
                               best_mode, dmarc_domain, dmarc_policy)
        )
        return'''

if old_tpl in content:
    content = content.replace(old_tpl, new_tpl, 1)
    changes += 1
    print("OK - template spoof: removed proxy, added background task")
else:
    print("WARN - template spoof block not found exactly")

# ============================================================
# 4. Add _bg_tpl_spoof_test background function
# ============================================================
bg_tpl_func = '''
async def _bg_tpl_spoof_test(bot, chat_id, smtp_config, tpl, to_email,
                              spoof_mode, dmarc_domain, dmarc_policy):
    """Background template spoof test — no proxy, direct connection."""
    from .campaign_sender import _send_one

    try:
        result = await asyncio.to_thread(
            _send_one,
            smtp_config,
            tpl["from_name"],
            tpl["from_email"],
            to_email,
            tpl["subject"],
            tpl["html"],
            "",
            smtp_config.get("from_email", ""),
            tpl.get("reply_to", ""),
            spoof_mode,
            None,
        )

        if result["success"]:
            mode_used = result.get("spoof_mode_used", spoof_mode)
            dmarc_warn = ""
            if dmarc_policy == "reject":
                dmarc_warn = (
                    "\\nATTENTION: DMARC p=reject\\n"
                    "Gmail/Outlook vont probablement DROP ce mail."
                )
            elif dmarc_policy == "quarantine":
                dmarc_warn = "\\nDMARC p=quarantine — le mail peut aller en SPAM."
            text = (
                "=============================\\n"
                f"  {tpl['brand']} SPOOF — ENVOYE !\\n"
                "=============================\\n\\n"
                f"Template: {tpl['brand']}\\n"
                f"From: {tpl['from_name']} <{tpl['from_email']}>\\n"
                f"To: {to_email}\\n"
                f"Mode: {mode_used}\\n"
                f"DMARC {dmarc_domain}: p={dmarc_policy}"
                f"{dmarc_warn}\\n\\n"
                "Verifie la boite mail !"
            )
        else:
            text = (
                f"{tpl['brand']} SPOOF — ECHEC\\n\\n"
                f"Erreur: {result.get('error', 'inconnue')}\\n"
                f"SMTP: {smtp_config['host']}:{smtp_config['port']}\\n"
                f"Mode: {result.get('spoof_mode_used', spoof_mode)}"
            )

        await bot.send_message(chat_id=chat_id, text=text[:4000])
    except Exception as e:
        try:
            await bot.send_message(chat_id=chat_id,
                text=f"TPL SPOOF — ERREUR\\n{type(e).__name__}: {str(e)[:200]}")
        except Exception:
            pass


'''

# Insert before _bg_spoof_test or before run_sender
if "async def _bg_spoof_test(" in content:
    content = content.replace("async def _bg_spoof_test(", bg_tpl_func + "async def _bg_spoof_test(", 1)
    changes += 1
    print("OK - added _bg_tpl_spoof_test function")

print(f"TOTAL CHANGES: {changes}")

with open("auditguard/sender_bot.py", "w") as f:
    f.write(content)

print("PATCH COMPLETE")
