#!/usr/bin/env python3
"""
Patch: Fix _send_one_recipient infinite wait + as_completed blocking.
"""

SB = "/opt/darkvasend/auditguard/sender_bot.py"
with open(SB) as f:
    sb = f.read()

# 1. Reduce retry rounds
sb = sb.replace(
    "        for attempt in range(len(all_smtp) * 3):",
    "        for attempt in range(len(all_smtp)):"
)

# 2. Reduce wait loop: 30*2s=60s -> 5*3s=15s
sb = sb.replace(
    '''                for wait_attempt in range(30):  # Wait up to 60s
                    time.sleep(2)''',
    '''                for wait_attempt in range(5):  # Wait up to 15s
                    time.sleep(3)'''
)

# 3. Reduce cooldowns
sb = sb.replace(
    "    COOLDOWN_MAX = 120           # Max cooldown seconds",
    "    COOLDOWN_MAX = 30            # Max cooldown seconds"
)
sb = sb.replace(
    "    COOLDOWN_BASE = 10           # Seconds cooldown after error (doubles each time)",
    "    COOLDOWN_BASE = 5            # Seconds cooldown after error (doubles each time)"
)

# 4. Reduce workers
sb = sb.replace(
    "    num_workers = min(len(smtp_list) * 2, 100)",
    "    num_workers = min(len(smtp_list), 44)"
)

# 5. Add timeout to as_completed + handle stuck futures
old_collect_block = '''            # Collect results — with DB error protection
            for f in as_completed(futures):
                try:
                    result = f.result()
                except Exception as e:
                    result = {"ok": False, "error": str(e)[:80],
                              "recipient_id": futures[f]["id"], "smtp": None}

                with lock:'''

new_collect_block = '''            # Collect results — with timeout to prevent infinite block
            _timed_out = False
            try:
              for f in as_completed(futures, timeout=90):
                try:
                    result = f.result(timeout=5)
                except Exception as e:
                    result = {"ok": False, "error": str(e)[:80],
                              "recipient_id": futures[f]["id"], "smtp": None}

                with lock:'''

sb = sb.replace(old_collect_block, new_collect_block)

# Find the end of the collect loop and add TimeoutError handling
old_progress = '''            # Progress every 50 sends (with SMTP health)'''
new_progress = '''            except TimeoutError:
                print("[CAMP] Batch timeout 90s — resetting stuck recipients", flush=True)
                for f in futures:
                    if not f.done():
                        r = futures[f]
                        try:
                            mgr.db.execute("UPDATE campaign_recipients SET status=\\'pending\\' WHERE id=?", (r["id"],))
                        except Exception:
                            pass
                try:
                    mgr.db.commit()
                except Exception:
                    pass

            # Progress every 50 sends (with SMTP health)'''

sb = sb.replace(old_progress, new_progress)

with open(SB, "w") as f:
    f.write(sb)

print("DONE")
