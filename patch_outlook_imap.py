#!/usr/bin/env python3
"""
Patch script — adds Outlook IMAP checker to imap_checker.py on the server.
Run on the server: python3 patch_outlook_imap.py
"""
import os

IMAP_FILE = "/opt/darkvasend/auditguard/imap_checker.py"

OUTLOOK_BLOCK = '''

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
    auth_string = f"user={email}\\x01auth=Bearer {access_token}\\x01\\x01"
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
'''

# Read current file
with open(IMAP_FILE, "r") as f:
    content = f.read()

# Check if already patched
if "OUTLOOK_CHECKER" in content:
    print("Already patched!")
else:
    # Append Outlook checker code before the last function definitions
    # Find a good insertion point - after GMAIL_CHECKER section
    marker = "def is_gmail_checker_configured"
    idx = content.find(marker)
    if idx == -1:
        # Just append at end
        content += OUTLOOK_BLOCK
    else:
        # Find end of get_gmail_checker_email function
        marker2 = "def get_gmail_checker_email"
        idx2 = content.find(marker2)
        if idx2 != -1:
            # Find next blank line after the function
            next_func = content.find("\n\n\n", idx2)
            if next_func == -1:
                next_func = content.find("\ndef ", idx2 + 50)
            if next_func != -1:
                content = content[:next_func] + "\n" + OUTLOOK_BLOCK + "\n" + content[next_func:]
            else:
                content += OUTLOOK_BLOCK
        else:
            content += OUTLOOK_BLOCK

    # Write back
    with open(IMAP_FILE, "w") as f:
        f.write(content)
    print("Patched imap_checker.py with Outlook checker!")
    print(f"File size: {len(content)} bytes")
