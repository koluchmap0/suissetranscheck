#!/usr/bin/env python3
"""
FIX: IMAP classification — SPAM first + double-check INBOX markers.

Problem: Emails checked too early land in INBOX because Gmail hasn't
         moved them to Spam yet. The old code checks INBOX first, so
         they get classified as INBOX when they should be SPAM.

Fix:
  1. Wait 90s instead of 60s (give Gmail more time to classify)
  2. Check SPAM FIRST, then INBOX (spam gets priority)
  3. Double-check: markers found in INBOX are re-verified 45s later
     in SPAM. If Gmail moved them → reclassify as SPAM.
"""
import re
import sys

TARGET = "/opt/darkvasend/auditguard/sender_bot.py"

# Read current file
with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

# ============================================================
# FIX 1: Replace wait time 60s → 90s
# ============================================================
old_wait = '    # === PHASE 2: Wait ===\n    await asyncio.sleep(60)'
new_wait = '    # === PHASE 2: Wait (90s for Gmail spam classification) ===\n    await asyncio.sleep(90)'

if old_wait in content:
    content = content.replace(old_wait, new_wait)
    print("[OK] Wait time: 60s → 90s")
else:
    print("[SKIP] Wait time already changed or not found")

# ============================================================
# FIX 2: Replace _batch_imap to check SPAM FIRST
# ============================================================
old_batch = '''    def _batch_imap(markers_to_check):
        import imaplib
        import ssl as _ssl
        results = {}
        try:
            ctx_ssl = _ssl.create_default_context()
            ctx_ssl.check_hostname = False
            ctx_ssl.verify_mode = _ssl.CERT_NONE
            imap = imaplib.IMAP4_SSL(
                GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
                ssl_context=ctx_ssl, timeout=30,
            )
            imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

            try:
                st, _ = imap.select("INBOX", readonly=True)
                if st == "OK":
                    for m in markers_to_check:
                        st2, ids = imap.search(None, f\'SUBJECT "{m}"\')
                        if st2 == "OK" and ids[0] and ids[0].strip():
                            results[m] = "inbox"
            except Exception:
                pass

            remaining = [m for m in markers_to_check if m not in results]
            if remaining:
                try:
                    st, _ = imap.select("[Gmail]/Spam", readonly=True)
                    if st == "OK":
                        for m in remaining:
                            st2, ids = imap.search(None, f\'SUBJECT "{m}"\')
                            if st2 == "OK" and ids[0] and ids[0].strip():
                                results[m] = "spam"
                except Exception:
                    pass

            imap.logout()
        except Exception as e:
            print(f"[MASS IMAP] error: {e}")
        return results'''

new_batch = '''    def _batch_imap(markers_to_check):
        """Check SPAM FIRST then INBOX — spam classification gets priority."""
        import imaplib
        import ssl as _ssl
        results = {}
        try:
            ctx_ssl = _ssl.create_default_context()
            ctx_ssl.check_hostname = False
            ctx_ssl.verify_mode = _ssl.CERT_NONE
            imap = imaplib.IMAP4_SSL(
                GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
                ssl_context=ctx_ssl, timeout=30,
            )
            imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

            # === CHECK SPAM FIRST (priority) ===
            try:
                st, _ = imap.select("[Gmail]/Spam", readonly=True)
                if st == "OK":
                    for m in markers_to_check:
                        st2, ids = imap.search(None, f\'SUBJECT "{m}"\')
                        if st2 == "OK" and ids[0] and ids[0].strip():
                            results[m] = "spam"
            except Exception:
                pass

            # === THEN CHECK INBOX (only markers not found in spam) ===
            remaining = [m for m in markers_to_check if m not in results]
            if remaining:
                try:
                    st, _ = imap.select("INBOX", readonly=True)
                    if st == "OK":
                        for m in remaining:
                            st2, ids = imap.search(None, f\'SUBJECT "{m}"\')
                            if st2 == "OK" and ids[0] and ids[0].strip():
                                results[m] = "inbox"
                except Exception:
                    pass

            imap.logout()
        except Exception as e:
            print(f"[MASS IMAP] error: {e}")
        return results'''

if old_batch in content:
    content = content.replace(old_batch, new_batch)
    print("[OK] _batch_imap: SPAM first, then INBOX")
else:
    print("[WARN] _batch_imap not found exactly — trying flexible match")
    # Try to find and replace with regex
    pattern = r'(    def _batch_imap\(markers_to_check\):.*?return results)'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        content = content[:match.start()] + new_batch + content[match.end():]
        print("[OK] _batch_imap replaced via regex")
    else:
        print("[ERROR] Could not find _batch_imap function!")
        sys.exit(1)

# ============================================================
# FIX 3: Add double-check for INBOX markers after retry
# ============================================================
old_retry = '''    imap_results = await asyncio.to_thread(_batch_imap, all_markers)

    missing = [m for m in all_markers if m not in imap_results]
    if missing:
        await _send(f"1er check: {len(imap_results)}/{total_markers}. Retry 20s...")
        await asyncio.sleep(20)
        r2 = await asyncio.to_thread(_batch_imap, missing)
        imap_results.update(r2)'''

new_retry = '''    imap_results = await asyncio.to_thread(_batch_imap, all_markers)

    missing = [m for m in all_markers if m not in imap_results]
    if missing:
        await _send(f"1er check: {len(imap_results)}/{total_markers}. Retry 30s...")
        await asyncio.sleep(30)
        r2 = await asyncio.to_thread(_batch_imap, missing)
        imap_results.update(r2)

    # === DOUBLE-CHECK: Re-verify INBOX markers in SPAM after 45s ===
    # Gmail can take time to move emails from INBOX to Spam
    inbox_markers = [m for m, v in imap_results.items() if v == "inbox"]
    if inbox_markers:
        inbox_count_before = len(inbox_markers)
        await _send(f"DOUBLE-CHECK: {inbox_count_before} markers INBOX — re-verification dans 45s...")
        await asyncio.sleep(45)

        def _recheck_spam(markers_to_recheck):
            """Re-check if INBOX markers moved to SPAM."""
            import imaplib
            import ssl as _ssl
            moved = {}
            try:
                ctx_ssl = _ssl.create_default_context()
                ctx_ssl.check_hostname = False
                ctx_ssl.verify_mode = _ssl.CERT_NONE
                imap = imaplib.IMAP4_SSL(
                    GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
                    ssl_context=ctx_ssl, timeout=30,
                )
                imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

                # Check SPAM for these markers
                st, _ = imap.select("[Gmail]/Spam", readonly=True)
                if st == "OK":
                    for m in markers_to_recheck:
                        st2, ids = imap.search(None, f\'SUBJECT "{m}"\')
                        if st2 == "OK" and ids[0] and ids[0].strip():
                            moved[m] = "spam"

                # Also verify they are still in INBOX
                st, _ = imap.select("INBOX", readonly=True)
                if st == "OK":
                    for m in markers_to_recheck:
                        if m not in moved:
                            st2, ids = imap.search(None, f\'SUBJECT "{m}"\')
                            if not (st2 == "OK" and ids[0] and ids[0].strip()):
                                # Not in INBOX anymore and not in SPAM = disappeared
                                moved[m] = "not_found"

                imap.logout()
            except Exception as e:
                print(f"[RECHECK] error: {e}")
            return moved

        recheck = await asyncio.to_thread(_recheck_spam, inbox_markers)
        reclassified = 0
        for m, new_status in recheck.items():
            if new_status == "spam":
                imap_results[m] = "spam"
                reclassified += 1
                print(f"[RECHECK] {m}: INBOX -> SPAM (reclassified)")
            elif new_status == "not_found":
                imap_results[m] = "not_found"
                print(f"[RECHECK] {m}: INBOX -> NOT FOUND (removed)")

        if reclassified > 0:
            await _send(f"DOUBLE-CHECK: {reclassified} markers reclassifies INBOX -> SPAM")
        else:
            await _send(f"DOUBLE-CHECK OK: {inbox_count_before} markers confirmes INBOX")'''

if old_retry in content:
    content = content.replace(old_retry, new_retry)
    print("[OK] Added double-check for INBOX markers")
else:
    print("[WARN] Retry block not found exactly")

# Write back
with open(TARGET, "w", encoding="utf-8") as f:
    f.write(content)

print("\n[DONE] All fixes applied to", TARGET)
