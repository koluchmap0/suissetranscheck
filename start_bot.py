#!/usr/bin/env python3
"""Launcher for BUST GO Sender Bot — single instance."""
import os
import sys
import fcntl

LOCK_FILE = "/tmp/bustgo_sender.lock"

# Ensure single instance
lock_fp = open(LOCK_FILE, "w")
try:
    fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("Bot already running — exiting.")
    sys.exit(0)

lock_fp.write(str(os.getpid()))
lock_fp.flush()

os.environ["SENDER_BOT_TOKEN"] = "8639285552:AAHTpBVbUDnlNuhrJnEelTo7WawKu1GAuuk"
os.environ["SENDER_ADMIN_IDS"] = "7047311463"

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from auditguard.sender_bot import run_sender
run_sender()
