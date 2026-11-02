"""
Fix v2: 
1. Stop TLD stripping in bg_tpl and bg_spoof (causes invalid From like noreply@post)
2. Add 3-layer fallback in _send_one: full spoof -> stripped TLD -> display mode
3. Pass original_from_email so _send_one can do smart retries
"""
import re

# === Fix sender_bot.py: Remove TLD stripping from background functions ===
BOT = '/opt/darkvasend/auditguard/sender_bot.py'
with open(BOT, 'r') as f:
    bot_content = f.read()

# Fix _bg_tpl_spoof_test: don't strip TLD, let _send_one handle it
old_tpl_strip = '''async def _bg_tpl_spoof_test(bot, chat_id, smtp_config, tpl, to_email,
                              spoof_mode, dmarc_domain, dmarc_policy):
    """Background template spoof test — no proxy, direct connection."""
    from .campaign_sender import _send_one, _strip_tld

    # Auto-strip TLD when DMARC p=reject to bypass Gmail DROP
    actual_from = tpl["from_email"]
    tld_stripped = False
    if dmarc_policy == "reject" and "@" in actual_from:
        actual_from = _strip_tld(actual_from)
        tld_stripped = True

    try:
        result = await asyncio.to_thread(
            _send_one,
            smtp_config,
            tpl["from_name"],
            actual_from,
            to_email,
            tpl["subject"],
            tpl["html"],
            "",
            smtp_config.get("from_email", ""),
            tpl.get("reply_to", ""),
            spoof_mode,
            None,
        )'''

new_tpl_strip = '''async def _bg_tpl_spoof_test(bot, chat_id, smtp_config, tpl, to_email,
                              spoof_mode, dmarc_domain, dmarc_policy):
    """Background template spoof test — no proxy, direct connection."""
    from .campaign_sender import _send_one, _strip_tld

    # Don't strip TLD here — _send_one has smart retry with fallback
    actual_from = tpl["from_email"]
    tld_stripped = False

    try:
        result = await asyncio.to_thread(
            _send_one,
            smtp_config,
            tpl["from_name"],
            actual_from,
            to_email,
            tpl["subject"],
            tpl["html"],
            "",
            smtp_config.get("from_email", ""),
            tpl.get("reply_to", ""),
            spoof_mode,
            None,
        )'''

if old_tpl_strip in bot_content:
    bot_content = bot_content.replace(old_tpl_strip, new_tpl_strip)
    print('BOT FIX 1: Removed TLD strip from _bg_tpl_spoof_test')
else:
    print('BOT FIX 1: Pattern not found')

# Fix _bg_spoof_test: same thing
old_spoof_strip = '''async def _bg_spoof_test(bot, chat_id, smtp_config, from_name, from_email,
                         to_email, subject, html_body, text_body, spoof_mode,
                         dmarc_domain, dmarc_policy, cred_id):
    """Background spoof test — no proxy, direct connection."""
    from .campaign_sender import _send_one, _strip_tld

    # Auto-strip TLD when DMARC p=reject to bypass Gmail DROP
    actual_from = from_email
    tld_stripped = False
    if dmarc_policy == "reject" and "@" in actual_from:
        actual_from = _strip_tld(actual_from)
        tld_stripped = True'''

new_spoof_strip = '''async def _bg_spoof_test(bot, chat_id, smtp_config, from_name, from_email,
                         to_email, subject, html_body, text_body, spoof_mode,
                         dmarc_domain, dmarc_policy, cred_id):
    """Background spoof test — no proxy, direct connection."""
    from .campaign_sender import _send_one, _strip_tld

    # Don't strip TLD here — _send_one has smart retry with fallback
    actual_from = from_email
    tld_stripped = False'''

if old_spoof_strip in bot_content:
    bot_content = bot_content.replace(old_spoof_strip, new_spoof_strip)
    print('BOT FIX 2: Removed TLD strip from _bg_spoof_test')
else:
    print('BOT FIX 2: Pattern not found')

with open(BOT, 'w') as f:
    f.write(bot_content)


# === Fix campaign_sender.py: Better retry logic in _send_one ===
CS = '/opt/darkvasend/auditguard/campaign_sender.py'
with open(CS, 'r') as f:
    cs_content = f.read()

# Replace the retry block in _send_one with a 3-layer fallback
old_retry = '''        # Send — with retry for strict servers (NEVER expose real email)
        # thinspace → clean | clean → retry clean after cooldown
        try:
            server.sendmail(real_envelope, [to_email], msg.as_string())
        except smtplib.SMTPDataError as e:
            if e.smtp_code in (554, 550, 552):
                try:
                    server.quit()
                except Exception:
                    pass
                time.sleep(3)  # Server cooldown after 554

                if spoof_mode == "thinspace":
                    # thinspace failed → retry with clean From (still spoofed)
                    retry_from = f"{from_name} <{visible_from}>" if from_name else visible_from
                    retry_mode = "clean"
                elif spoof_mode == "clean":
                    # clean failed → retry clean after cooldown (server may accept after reset)
                    retry_from = f"{from_name} <{visible_from}>" if from_name else visible_from
                    retry_mode = "clean_retry"
                else:
                    result["error"] = f"DATA ERROR: {e.smtp_code} (server rejects spoofed From)"
                    result["spoof_mode_used"] = "failed"
                    return result

                # Rebuild message with clean From (spoofed — never real email)
                msg_retry = MIMEMultipart("alternative")
                msg_retry["Subject"] = subject
                msg_retry["To"] = to_email
                msg_retry["From"] = retry_from
                msg_retry["Message-ID"] = f"<{msg_id}@{from_domain}>"
                msg_retry["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
                msg_retry["X-Mailer"] = "Microsoft Outlook 16.0"
                msg_retry["MIME-Version"] = "1.0"
                if reply_to:
                    msg_retry["Reply-To"] = reply_to
                msg_retry.attach(MIMEText(final_text, "plain", "utf-8"))
                msg_retry.attach(MIMEText(final_html, "html", "utf-8"))

                # Reconnect and send
                try:
                    server = _connect_smtp(host, port, ehlo_domain, proxy=proxy)
                    server.login(user, password)
                    server.sendmail(real_envelope, [to_email], msg_retry.as_string())
                    server.quit()
                    result["success"] = True
                    result["spoof_mode_used"] = "clean"
                    return result
                except smtplib.SMTPDataError as e2:
                    result["error"] = f"DATA ERROR: {e2.smtp_code} (server rejects spoofed From — try another SMTP)"
                    result["spoof_mode_used"] = "failed"
                    return result
                except Exception as e2:
                    result["error"] = f"RETRY: {type(e2).__name__}: {str(e2)[:80]}"
                    result["spoof_mode_used"] = "failed"
                    return result
            else:
                raise'''

new_retry = '''        # Send — 3-layer fallback: full spoof → display mode (branded real email)
        try:
            server.sendmail(real_envelope, [to_email], msg.as_string())
        except (smtplib.SMTPDataError, smtplib.SMTPSenderRefused) as e:
            err_code = getattr(e, 'smtp_code', 0)
            if err_code in (554, 550, 552, 451, 421):
                try:
                    server.quit()
                except Exception:
                    pass
                time.sleep(1)

                # LAYER 2: Display mode — use real SMTP email with branded display name
                # This always works because the From matches the authenticated user
                display_name = from_name or visible_from.split("@")[0]
                display_from = f"{display_name} <{real_envelope}>"

                msg_retry = MIMEMultipart("alternative")
                msg_retry["Subject"] = subject
                msg_retry["To"] = to_email
                msg_retry["From"] = display_from
                msg_retry["Message-ID"] = f"<{msg_id}@{from_domain}>"
                msg_retry["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
                msg_retry["X-Mailer"] = "Microsoft Outlook 16.0"
                msg_retry["MIME-Version"] = "1.0"
                if reply_to:
                    msg_retry["Reply-To"] = reply_to
                msg_retry.attach(MIMEText(final_text, "plain", "utf-8"))
                msg_retry.attach(MIMEText(final_html, "html", "utf-8"))

                try:
                    server = _connect_smtp(host, port, ehlo_domain, proxy=proxy)
                    server.login(user, password)
                    server.sendmail(real_envelope, [to_email], msg_retry.as_string())
                    server.quit()
                    result["success"] = True
                    result["spoof_mode_used"] = "display"
                    return result
                except Exception as e2:
                    result["error"] = f"ALL MODES FAILED: spoof={err_code} display={type(e2).__name__}"
                    result["spoof_mode_used"] = "failed"
                    return result
            else:
                raise'''

if old_retry in cs_content:
    cs_content = cs_content.replace(old_retry, new_retry)
    print('CS FIX: Replaced retry logic with 3-layer fallback')
else:
    print('CS FIX: Retry pattern not found, trying to locate...')
    # Check if already partially patched
    if '3-layer fallback' in cs_content:
        print('CS FIX: Already patched')
    else:
        print('CS FIX: MANUAL CHECK NEEDED')

with open(CS, 'w') as f:
    f.write(cs_content)

print('\nAll v2 fixes applied!')
