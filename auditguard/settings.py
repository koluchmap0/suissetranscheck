"""Simple settings store — file-based for CLI, can be overridden by Telegram/PG."""
from __future__ import annotations

import json
import os
from pathlib import Path

SETTINGS_PATH = Path(os.getenv("BUSTGO_SETTINGS", "bustgo-settings.json"))


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_settings(settings: dict):
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def get_notify_email() -> str | None:
    return load_settings().get("notify_email")


def set_notify_email(email: str):
    s = load_settings()
    s["notify_email"] = email
    save_settings(s)


def get_telegram_chat_id() -> int | None:
    return load_settings().get("telegram_chat_id")


def set_telegram_chat_id(chat_id: int):
    s = load_settings()
    s["telegram_chat_id"] = chat_id
    save_settings(s)
