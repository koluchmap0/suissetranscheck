#!/usr/bin/env python3
"""
Patch 3: DEFINITIVE fix.
1. Add global _CAMPAIGN_ACTIVE flag
2. _run_swisspost_campaign sets/clears it
3. Health check checks it (not _running_campaigns which is never set)
4. Health check uses short-lived DB connections (open-write-close per SMTP)
"""

SB_PATH = "/opt/darkvasend/auditguard/sender_bot.py"

with open(SB_PATH, "r") as f:
    sb = f.read()

# ============================================================
# 1. Add global flag right before _run_swisspost_campaign
# ============================================================
if "_CAMPAIGN_ACTIVE" not in sb:
    sb = sb.replace(
        "async def _run_swisspost_campaign(campaign_id, db_path, chat_id, mode=\"spoof\"):",
        "_CAMPAIGN_ACTIVE = False  # Global flag: True when any campaign is sending\n\nasync def _run_swisspost_campaign(campaign_id, db_path, chat_id, mode=\"spoof\"):"
    )
    print("Added _CAMPAIGN_ACTIVE flag")

# ============================================================
# 2. Set _CAMPAIGN_ACTIVE = True at start of campaign
# ============================================================
old_update_running = '    mgr.update_campaign_status(campaign_id, "running")\n\n    rate = camp["rate_per_minute"] or 30'
new_update_running = '    mgr.update_campaign_status(campaign_id, "running")\n\n    global _CAMPAIGN_ACTIVE\n    _CAMPAIGN_ACTIVE = True\n\n    rate = camp["rate_per_minute"] or 30'
sb = sb.replace(old_update_running, new_update_running)
print("Set _CAMPAIGN_ACTIVE = True at campaign start")

# ============================================================
# 3. Clear _CAMPAIGN_ACTIVE at end of campaign (in the finally/status section)
# ============================================================
# Find the campaign end section
old_camp_end = '''    try:
        status = "finished" if not mgr.claim_recipient(campaign_id) else "stopped"
    except Exception:
        status = "stopped"
    try:
        mgr.update_campaign_status(campaign_id, status)
    except Exception:
        pass'''

new_camp_end = '''    global _CAMPAIGN_ACTIVE
    _CAMPAIGN_ACTIVE = False

    try:
        status = "finished" if not mgr.claim_recipient(campaign_id) else "stopped"
    except Exception:
        status = "stopped"
    try:
        mgr.update_campaign_status(campaign_id, status)
    except Exception:
        pass'''
sb = sb.replace(old_camp_end, new_camp_end)
print("Set _CAMPAIGN_ACTIVE = False at campaign end")

# ============================================================
# 4. Fix health check to use _CAMPAIGN_ACTIVE instead of _running_campaigns
# ============================================================
old_hc_skip = '''            # SKIP health check if any campaign is running
            from .campaign_sender import _running_campaigns
            if _running_campaigns:
                active = [k for k, v in _running_campaigns.items() if v]
                if active:
                    print(f"[IMAP CHECK] SKIPPED — campaigns running: {active}", flush=True)
                    continue'''

new_hc_skip = '''            # SKIP health check if any campaign is running
            if _CAMPAIGN_ACTIVE:
                print("[IMAP CHECK] SKIPPED — campaign is active", flush=True)
                continue'''
sb = sb.replace(old_hc_skip, new_hc_skip)
print("Fixed health check skip to use _CAMPAIGN_ACTIVE")

# Also fix the mid-loop campaign check
old_mid_check = '''                # Check again if campaign started while we're running
                from .campaign_sender import _running_campaigns as _rc2
                if any(_rc2.values()):
                    print("[IMAP CHECK] Campaign started — aborting health check", flush=True)
                    break'''

new_mid_check = '''                # Check again if campaign started while we're running
                if _CAMPAIGN_ACTIVE:
                    print("[IMAP CHECK] Campaign started — aborting health check", flush=True)
                    break'''
sb = sb.replace(old_mid_check, new_mid_check)
print("Fixed mid-loop campaign check")

# ============================================================
# 5. Also add _CAMPAIGN_ACTIVE to auto-resume section
# ============================================================
# Find auto-resume to ensure it sets the flag
old_auto_resume = '    asyncio.create_task(_run_swisspost_campaign(_cid, DB_PATH, int(list(ADMIN_IDS)[0]) if ADMIN_IDS else 0, \'inbox\'))'
if old_auto_resume in sb:
    # The flag is set inside _run_swisspost_campaign so this is OK
    print("Auto-resume uses _run_swisspost_campaign which sets the flag")

with open(SB_PATH, "w") as f:
    f.write(sb)

print("\nALL PATCHES APPLIED")
