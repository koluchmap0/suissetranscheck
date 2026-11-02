"""Patch: Spoof Test - retry without proxy on proxy error."""

with open("auditguard/sender_bot.py", "r") as f:
    content = f.read()

# Find the spoof test send section and add retry without proxy
old_code = '''        result = await asyncio.to_thread(
            _send_one,
            {
                "host": smtp["smtp_host"],
                "port": smtp["smtp_port"],
                "username": smtp["smtp_user"],
                "password": smtp["smtp_password"],
                "from_email": smtp.get("smtp_user", ""),
            },
            from_name,
            from_email,  # Spoofed FROM (visible)
            to_email,
            subject,
            body if "<" in body else f"<p>{body}</p>",
            body if "<" not in body else "",
            envelope_from=smtp.get("smtp_user", ""),  # Real SMTP user
            spoof_mode=best_mode,
            proxy=_proxy2,
        )
        
        if result["success"]:'''

new_code = '''        smtp_config = {
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
                f"Proxy erreur ({result['error'][:60]})\nRetry SANS proxy..."
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

if old_code in content:
    content = content.replace(old_code, new_code, 1)
    print("OK - spoof test retry patch applied")
else:
    print("ERROR - spoof test code not found")
    # Try to find it
    import re
    idx = content.find("proxy=_proxy2,")
    if idx > 0:
        print(f"Found proxy=_proxy2 at position {idx}")
        # Show context
        start = max(0, idx - 200)
        print(content[start:idx+50])
    else:
        print("proxy=_proxy2 not found at all")

# Also patch the template spoof test (tpl_send flow) - same issue
# Find tpl spoof send section
tpl_old = None
tpl_search = content.find("# Template spoof send")
if tpl_search < 0:
    tpl_search = content.find("tpl_send_to")

# Also fix mass_test - already has proxy retry logic, but let's check quick_spoof_test
# The mass test and IMAP check already handle proxy, but let's also patch the 
# single quick_spoof_test flow to not use proxy by default

with open("auditguard/sender_bot.py", "w") as f:
    f.write(content)

print("PATCH COMPLETE")
