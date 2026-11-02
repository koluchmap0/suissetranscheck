"""
BUST GO — Proxy Pool.
SOCKS5/SOCKS4 proxy management with round-robin rotation for SMTP sends.

Features:
  - Add/remove/list proxies (SQLite-backed)
  - Round-robin rotation
  - Success/fail tracking per proxy
  - Auto-deactivate on repeated failures
  - SOCKS5 socket creation for smtplib
"""
from __future__ import annotations

import os
import random
import sqlite3
import time
from urllib.parse import urlparse

import socks

from .db import DEFAULT_DB

DB_PATH = os.getenv("BUSTGO_HUNT_DB", DEFAULT_DB)

# Auto-deactivate after this many consecutive failures
MAX_CONSECUTIVE_FAILS = 5


class ProxyPool:
    """SQLite-backed proxy pool with round-robin rotation."""

    def __init__(self, db_path: str = DB_PATH):
        self.db = sqlite3.connect(db_path, timeout=120, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=120000")
        # Ensure table exists (may already exist from db.py schema)
        self.db.executescript("""
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
        """)
        self.db.commit()
        self._robin_idx = 0

    def add_proxy(self, url: str, label: str = "") -> int | None:
        """
        Add a proxy. Accepts formats:
          socks5://user:pass@host:port
          socks5://host:port
          host:port (assumes socks5, no auth)
          host:port|user:pass
        Returns proxy id or None if duplicate.
        """
        parsed = self._parse_proxy_url(url)
        if not parsed:
            return None

        canon_url = self._canonical_url(parsed)

        try:
            with self.db:
                cur = self.db.execute(
                    """INSERT INTO proxies
                       (url, proxy_type, host, port, username, password, label, is_active, added_at)
                       VALUES (?,?,?,?,?,?,?,1,?)""",
                    (canon_url, parsed["type"], parsed["host"], parsed["port"],
                     parsed["user"], parsed["pass"], label, time.time()),
                )
                return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # Duplicate

    def remove_proxy(self, proxy_id: int):
        with self.db:
            self.db.execute("DELETE FROM proxies WHERE id=?", (proxy_id,))

    def get_all(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM proxies ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_active(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM proxies WHERE is_active=1 ORDER BY success_count DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, proxy_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM proxies WHERE id=?", (proxy_id,)).fetchone()
        return dict(row) if row else None

    def count_active(self) -> int:
        return self.db.execute("SELECT COUNT(*) n FROM proxies WHERE is_active=1").fetchone()["n"]

    def get_next(self) -> dict | None:
        """Round-robin: get next active proxy."""
        active = self.get_active()
        if not active:
            return None
        proxy = active[self._robin_idx % len(active)]
        self._robin_idx += 1
        return proxy

    def get_random(self) -> dict | None:
        """Get a random active proxy."""
        active = self.get_active()
        return random.choice(active) if active else None

    def mark_success(self, proxy_id: int):
        with self.db:
            self.db.execute(
                "UPDATE proxies SET success_count=success_count+1, fail_count=0, last_check=?, last_error='' WHERE id=?",
                (time.time(), proxy_id),
            )

    def mark_fail(self, proxy_id: int, error: str = ""):
        with self.db:
            self.db.execute(
                "UPDATE proxies SET fail_count=fail_count+1, last_check=?, last_error=? WHERE id=?",
                (time.time(), error[:500], proxy_id),
            )
            # Auto-deactivate on too many consecutive fails
            row = self.db.execute("SELECT fail_count FROM proxies WHERE id=?", (proxy_id,)).fetchone()
            if row and row["fail_count"] >= MAX_CONSECUTIVE_FAILS:
                self.db.execute("UPDATE proxies SET is_active=0 WHERE id=?", (proxy_id,))

    def activate(self, proxy_id: int):
        with self.db:
            self.db.execute("UPDATE proxies SET is_active=1, fail_count=0, last_error='' WHERE id=?", (proxy_id,))

    def deactivate(self, proxy_id: int):
        with self.db:
            self.db.execute("UPDATE proxies SET is_active=0 WHERE id=?", (proxy_id,))

    def stats(self) -> dict:
        total = self.db.execute("SELECT COUNT(*) n FROM proxies").fetchone()["n"]
        active = self.db.execute("SELECT COUNT(*) n FROM proxies WHERE is_active=1").fetchone()["n"]
        return {"total": total, "active": active, "inactive": total - active}

    def close(self):
        self.db.close()

    # --- Parsing helpers ---

    @staticmethod
    def _parse_proxy_url(raw: str) -> dict | None:
        """Parse various proxy URL formats into a dict."""
        raw = raw.strip()
        if not raw:
            return None

        # Format: host:port|user:pass
        if "|" in raw:
            parts = raw.split("|", 1)
            hp = parts[0].split(":")
            up = parts[1].split(":", 1)
            if len(hp) >= 2 and len(up) >= 2:
                try:
                    return {
                        "type": "socks5",
                        "host": hp[0].strip(),
                        "port": int(hp[1].strip()),
                        "user": up[0].strip(),
                        "pass": up[1].strip(),
                    }
                except ValueError:
                    return None

        # Format: socks5://user:pass@host:port  or  socks5://host:port
        if "://" in raw:
            parsed = urlparse(raw)
            ptype = parsed.scheme or "socks5"
            if ptype not in ("socks5", "socks4", "http", "https"):
                ptype = "socks5"
            return {
                "type": ptype,
                "host": parsed.hostname or "",
                "port": parsed.port or 1080,
                "user": parsed.username or "",
                "pass": parsed.password or "",
            }

        # Format: host:port (no auth)
        parts = raw.split(":")
        if len(parts) == 2:
            try:
                return {
                    "type": "socks5",
                    "host": parts[0].strip(),
                    "port": int(parts[1].strip()),
                    "user": "",
                    "pass": "",
                }
            except ValueError:
                return None

        return None

    @staticmethod
    def _canonical_url(parsed: dict) -> str:
        """Build canonical URL from parsed proxy dict."""
        auth = f"{parsed['user']}:{parsed['pass']}@" if parsed["user"] else ""
        return f"{parsed['type']}://{auth}{parsed['host']}:{parsed['port']}"


# ---------------------------------------------------------------------------
# SOCKS5 socket helper — used by campaign_sender for proxied SMTP
# ---------------------------------------------------------------------------

def make_socks_socket(proxy: dict, dest_host: str, dest_port: int, timeout: float = 15):
    """
    Create a SOCKS5/SOCKS4 socket connected to dest through proxy.
    
    Args:
        proxy: dict with keys: host, port, username, password, proxy_type
        dest_host: SMTP server hostname
        dest_port: SMTP server port
        timeout: connection timeout
    
    Returns connected socket.
    """
    ptype = (proxy.get("proxy_type") or "socks5").lower()
    proto = socks.SOCKS5 if "socks5" in ptype else socks.SOCKS4

    s = socks.socksocket()
    s.set_proxy(
        proto,
        proxy.get("host", "127.0.0.1"),
        proxy.get("port", 1080),
        username=proxy.get("username") or None,
        password=proxy.get("password") or None,
    )
    s.settimeout(timeout)
    s.connect((dest_host, dest_port))
    return s
