"""Patch: Template spoof test - retry without proxy on proxy error."""

with open("auditguard/sender_bot.py", "r") as f:
    content = f.read()

# Find the template spoof section
old_tpl = '''        from .campaign_sender import _send_one
        from .proxy_pool import ProxyPool
        _pool = ProxyPool(DB_PATH)
        _proxy = _pool.get_next() if _pool.count_active() > 0 else None
        _pool.close()

        result = await asyncio.to_thread(
            _send_one,
            {
                "host": smtp["smtp_host"],
                "port": smtp["smtp_port"],
                "username": smtp["smtp_user"],
                "password": smtp["smtp_password"],
                "from_email": smtp.get("smtp_user", ""),
            },
            tpl["from_name"],
            tpl["from_email"],
            text,
            tpl["subject"],
            tpl["html"],
            "",
            smtp.get("smtp_user", ""),
            tpl.get("reply_to", ""),
            best_mode,  # Auto-selected based on DMARC
            _proxy,
        )

        if result["success"]:'''

new_tpl = '''        from .campaign_sender import _send_one
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
                f"Proxy erreur ({result['error'][:60]})\nRetry SANS proxy..."
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

if old_tpl in content:
    content = content.replace(old_tpl, new_tpl, 1)
    print("OK - template spoof retry patch applied")
else:
    print("WARN - template spoof exact match not found")

with open("auditguard/sender_bot.py", "w") as f:
    f.write(content)

print("PATCH COMPLETE")
