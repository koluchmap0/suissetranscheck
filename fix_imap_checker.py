#!/usr/bin/env python3
"""
FIX: imap_checker.py — check_gmail_delivery + BatchDeliveryChecker
Same bug: checks INBOX first instead of SPAM first.
"""

TARGET = "/opt/darkvasend/auditguard/imap_checker.py"

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

# ============================================================
# FIX check_gmail_delivery: SPAM first
# ============================================================
old_delivery = '''    """Check Gmail for spoof with marker. Returns: inbox / spam / not_found / error"""
    folders_inbox = ["INBOX"]
    folders_spam = ["[Gmail]/Spam"]

    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(retry_delay)
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            imap = imaplib.IMAP4_SSL(
                GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
                ssl_context=ctx, timeout=timeout,
            )
            imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

            for folder in folders_inbox:
                try:
                    status, data = imap.select(folder, readonly=True)
                    if status != "OK":
                        continue
                    status, msg_ids = imap.search(None, f'(SUBJECT "{marker}")')
                    if status == "OK" and msg_ids[0] and msg_ids[0].strip():
                        imap.logout()
                        return "inbox"
                except Exception:
                    continue

            for folder in folders_spam:
                try:
                    status, data = imap.select(folder, readonly=True)
                    if status != "OK":
                        continue
                    status, msg_ids = imap.search(None, f'(SUBJECT "{marker}")')
                    if status == "OK" and msg_ids[0] and msg_ids[0].strip():
                        imap.logout()
                        return "spam"
                except Exception:
                    continue

            imap.logout()
        except Exception:
            if attempt == max_retries - 1:
                return "error"
            continue

    return "not_found"'''

new_delivery = '''    """Check Gmail for spoof with marker. Returns: inbox / spam / not_found / error.
    SPAM is checked FIRST to avoid false INBOX classification when Gmail
    hasn't finished spam processing yet."""
    folders_spam = ["[Gmail]/Spam"]
    folders_inbox = ["INBOX"]

    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(retry_delay)
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            imap = imaplib.IMAP4_SSL(
                GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
                ssl_context=ctx, timeout=timeout,
            )
            imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

            # CHECK SPAM FIRST (priority — avoids false inbox classification)
            for folder in folders_spam:
                try:
                    status, data = imap.select(folder, readonly=True)
                    if status != "OK":
                        continue
                    status, msg_ids = imap.search(None, f'(SUBJECT "{marker}")')
                    if status == "OK" and msg_ids[0] and msg_ids[0].strip():
                        imap.logout()
                        return "spam"
                except Exception:
                    continue

            # THEN CHECK INBOX
            for folder in folders_inbox:
                try:
                    status, data = imap.select(folder, readonly=True)
                    if status != "OK":
                        continue
                    status, msg_ids = imap.search(None, f'(SUBJECT "{marker}")')
                    if status == "OK" and msg_ids[0] and msg_ids[0].strip():
                        imap.logout()
                        return "inbox"
                except Exception:
                    continue

            imap.logout()
        except Exception:
            if attempt == max_retries - 1:
                return "error"
            continue

    return "not_found"'''

if old_delivery in content:
    content = content.replace(old_delivery, new_delivery)
    print("[OK] check_gmail_delivery: SPAM first, then INBOX")
else:
    print("[WARN] check_gmail_delivery not found exactly")

# ============================================================
# FIX BatchDeliveryChecker._batch_imap_check: SPAM first
# ============================================================
old_batch_checker = '''            # Check INBOX for all markers
            try:
                status, _ = imap.select("INBOX", readonly=True)
                if status == "OK":
                    for marker in markers:
                        status, msg_ids = imap.search(None, f'SUBJECT "{marker}"')
                        if status == "OK" and msg_ids[0] and msg_ids[0].strip():
                            results[marker] = "inbox"
            except Exception:
                pass

            # Check Spam for remaining markers
            remaining = [m for m in markers if m not in results]
            if remaining:
                try:
                    status, _ = imap.select("[Gmail]/Spam", readonly=True)
                    if status == "OK":
                        for marker in remaining:
                            status, msg_ids = imap.search(None, f'SUBJECT "{marker}"')
                            if status == "OK" and msg_ids[0] and msg_ids[0].strip():
                                results[marker] = "spam"
                except Exception:
                    pass'''

new_batch_checker = '''            # Check SPAM FIRST for all markers (priority)
            try:
                status, _ = imap.select("[Gmail]/Spam", readonly=True)
                if status == "OK":
                    for marker in markers:
                        status, msg_ids = imap.search(None, f'SUBJECT "{marker}"')
                        if status == "OK" and msg_ids[0] and msg_ids[0].strip():
                            results[marker] = "spam"
            except Exception:
                pass

            # Then check INBOX for remaining markers
            remaining = [m for m in markers if m not in results]
            if remaining:
                try:
                    status, _ = imap.select("INBOX", readonly=True)
                    if status == "OK":
                        for marker in remaining:
                            status, msg_ids = imap.search(None, f'SUBJECT "{marker}"')
                            if status == "OK" and msg_ids[0] and msg_ids[0].strip():
                                results[marker] = "inbox"
                except Exception:
                    pass'''

if old_batch_checker in content:
    content = content.replace(old_batch_checker, new_batch_checker)
    print("[OK] BatchDeliveryChecker._batch_imap_check: SPAM first, then INBOX")
else:
    print("[WARN] BatchDeliveryChecker not found exactly (may not exist)")

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(content)

print("\n[DONE] imap_checker.py fixed")
