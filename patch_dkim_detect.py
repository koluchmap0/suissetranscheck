#!/usr/bin/env python3
"""
PATCH: Add DKIM detection to MASS IMAP CHECK.
After finding a marker in INBOX/SPAM, fetch Authentication-Results header
to detect if the SMTP server added a DKIM signature.

DKIM + spoof = Gmail sees mismatch between DKIM domain and From: domain
→ strong spam signal. No DKIM = better for spoof inbox delivery.
"""
import re

TARGET = "/opt/darkvasend/auditguard/sender_bot.py"

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

# ============================================================
# REPLACE _batch_imap with DKIM-aware version
# ============================================================
old_batch = '''    def _batch_imap(markers_to_check):
        """Check SPAM FIRST then INBOX — spam classification gets priority."""
        import imaplib
        import ssl as _ssl
        results = {}
        try:
            ctx_ssl = _ssl.create_default_context()
            ctx_ssl.check_hostname = False
            ctx_ssl.verify_mode = _ssl.CERT_NONE
            imap = imaplib.IMAP4_SSL(
                GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
                ssl_context=ctx_ssl, timeout=30,
            )
            imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

            # === CHECK SPAM FIRST (priority) ===
            try:
                st, _ = imap.select("[Gmail]/Spam", readonly=True)
                if st == "OK":
                    for m in markers_to_check:
                        st2, ids = imap.search(None, f\'SUBJECT "{m}"\')
                        if st2 == "OK" and ids[0] and ids[0].strip():
                            results[m] = "spam"
            except Exception:
                pass

            # === THEN CHECK INBOX (only markers not found in spam) ===
            remaining = [m for m in markers_to_check if m not in results]
            if remaining:
                try:
                    st, _ = imap.select("INBOX", readonly=True)
                    if st == "OK":
                        for m in remaining:
                            st2, ids = imap.search(None, f\'SUBJECT "{m}"\')
                            if st2 == "OK" and ids[0] and ids[0].strip():
                                results[m] = "inbox"
                except Exception:
                    pass

            imap.logout()
        except Exception as e:
            print(f"[MASS IMAP] error: {e}")
        return results'''

new_batch = '''    def _batch_imap(markers_to_check):
        """Check SPAM FIRST then INBOX. Also detect DKIM signatures."""
        import imaplib
        import ssl as _ssl
        import re as _re
        # results: marker -> {"delivery": "inbox"/"spam", "dkim": True/False, "dkim_domain": ""}
        results = {}

        def _check_dkim(imap_conn, msg_id):
            """Fetch Authentication-Results header and detect DKIM pass."""
            try:
                st, data = imap_conn.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (AUTHENTICATION-RESULTS)])")
                if data and data[0] and isinstance(data[0], tuple):
                    raw = data[0][1].decode("utf-8", errors="replace")
                    has_dkim = "dkim=pass" in raw.lower()
                    dkim_domain = ""
                    dm = _re.search(r"dkim=pass.*?header\.(?:d|i)=@?([^\s;]+)", raw, _re.I | _re.S)
                    if dm:
                        dkim_domain = dm.group(1).strip()
                    return has_dkim, dkim_domain
            except Exception:
                pass
            return False, ""

        try:
            ctx_ssl = _ssl.create_default_context()
            ctx_ssl.check_hostname = False
            ctx_ssl.verify_mode = _ssl.CERT_NONE
            imap = imaplib.IMAP4_SSL(
                GMAIL_CHECKER["imap_host"], GMAIL_CHECKER["imap_port"],
                ssl_context=ctx_ssl, timeout=30,
            )
            imap.login(GMAIL_CHECKER["email"], GMAIL_CHECKER["password"])

            # === CHECK SPAM FIRST (priority) ===
            try:
                st, _ = imap.select("[Gmail]/Spam", readonly=True)
                if st == "OK":
                    for m in markers_to_check:
                        st2, ids = imap.search(None, f\'SUBJECT "{m}"\')
                        if st2 == "OK" and ids[0] and ids[0].strip():
                            msg_id = ids[0].decode().split()[-1]
                            has_dkim, dkim_domain = _check_dkim(imap, msg_id)
                            results[m] = {"delivery": "spam", "dkim": has_dkim, "dkim_domain": dkim_domain}
            except Exception:
                pass

            # === THEN CHECK INBOX (only markers not found in spam) ===
            remaining = [m for m in markers_to_check if m not in results]
            if remaining:
                try:
                    st, _ = imap.select("INBOX", readonly=True)
                    if st == "OK":
                        for m in remaining:
                            st2, ids = imap.search(None, f\'SUBJECT "{m}"\')
                            if st2 == "OK" and ids[0] and ids[0].strip():
                                msg_id = ids[0].decode().split()[-1]
                                has_dkim, dkim_domain = _check_dkim(imap, msg_id)
                                results[m] = {"delivery": "inbox", "dkim": has_dkim, "dkim_domain": dkim_domain}
                except Exception:
                    pass

            imap.logout()
        except Exception as e:
            print(f"[MASS IMAP] error: {e}")
        return results'''

if old_batch in content:
    content = content.replace(old_batch, new_batch)
    print("[OK] _batch_imap: DKIM detection added")
else:
    print("[ERROR] _batch_imap not found!")

# ============================================================
# Update imap_results usage — now returns dicts instead of strings
# The code after _batch_imap uses imap_results.get(marker, "not_found")
# and checks if v == "inbox" etc. We need to adapt.
# ============================================================

# Fix the first imap_results usage
old_usage1 = '    imap_results = await asyncio.to_thread(_batch_imap, all_markers)\n\n    missing = [m for m in all_markers if m not in imap_results]'
new_usage1 = '    imap_results_raw = await asyncio.to_thread(_batch_imap, all_markers)\n    # Store DKIM info separately\n    dkim_info = {}  # marker -> {"dkim": bool, "dkim_domain": str}\n    imap_results = {}  # marker -> "inbox"/"spam" (compat)\n    for m, info in imap_results_raw.items():\n        imap_results[m] = info["delivery"]\n        dkim_info[m] = {"dkim": info.get("dkim", False), "dkim_domain": info.get("dkim_domain", "")}\n\n    missing = [m for m in all_markers if m not in imap_results]'

if old_usage1 in content:
    content = content.replace(old_usage1, new_usage1)
    print("[OK] imap_results extraction updated (1st call)")
else:
    print("[WARN] imap_results 1st usage not found exactly")

# Fix retry
old_retry = '        r2 = await asyncio.to_thread(_batch_imap, missing)\n        imap_results.update(r2)'
new_retry = '        r2 = await asyncio.to_thread(_batch_imap, missing)\n        for m, info in r2.items():\n            imap_results[m] = info["delivery"]\n            dkim_info[m] = {"dkim": info.get("dkim", False), "dkim_domain": info.get("dkim_domain", "")}'

if old_retry in content:
    content = content.replace(old_retry, new_retry)
    print("[OK] Retry imap_results extraction updated")
else:
    print("[WARN] Retry usage not found exactly")

# ============================================================
# Update CLASSIFY section to include DKIM info in labels
# ============================================================
old_classify = '''    for key, info in smtp_results.items():
        s = info["smtp"]
        sp = info["spoof"]
        bq = info["basique"]
        basique_from = _build_basique_from(s["user"], brand_name)

        if sp == "inbox":
            spoof_inbox.append((s, basique_from))
            _add_manual_smtp({**s, "label": f"SPOOF_INBOX_{s['label']}"})
        elif sp == "spam":
            spoof_spam.append((s, basique_from))
            _add_manual_smtp({**s, "label": f"SPOOF_SPAM_{s['label']}"})
        elif bq == "inbox":
            basique_inbox.append((s, basique_from))
            _add_manual_smtp({**s, "label": f"BASIQUE_INBOX_{s['label']}"})
        elif bq == "spam":
            basique_spam.append((s, basique_from))
            _add_manual_smtp({**s, "label": f"BASIQUE_SPAM_{s['label']}"})
        else:
            dead_list.append((s, basique_from))'''

new_classify = '''    for key, info in smtp_results.items():
        s = info["smtp"]
        sp = info["spoof"]
        bq = info["basique"]
        basique_from = _build_basique_from(s["user"], brand_name)

        # Check DKIM for spoof and basique markers
        sp_marker = info.get("spoof_marker", "")
        bq_marker = info.get("basique_marker", "")
        sp_dkim = dkim_info.get(sp_marker, {}).get("dkim", False) if sp_marker else False
        bq_dkim = dkim_info.get(bq_marker, {}).get("dkim", False) if bq_marker else False
        sp_dkim_domain = dkim_info.get(sp_marker, {}).get("dkim_domain", "") if sp_marker else ""
        bq_dkim_domain = dkim_info.get(bq_marker, {}).get("dkim_domain", "") if bq_marker else ""

        # DKIM tag for label
        dkim_tag = "_DKIM" if (sp_dkim or bq_dkim) else "_NODKIM"
        dkim_d = sp_dkim_domain or bq_dkim_domain

        if sp == "inbox":
            spoof_inbox.append((s, basique_from, sp_dkim, dkim_d))
            _add_manual_smtp({**s, "label": f"SPOOF_INBOX{dkim_tag}_{s['label']}"})
        elif sp == "spam":
            spoof_spam.append((s, basique_from, sp_dkim, dkim_d))
            _add_manual_smtp({**s, "label": f"SPOOF_SPAM{dkim_tag}_{s['label']}"})
        elif bq == "inbox":
            basique_inbox.append((s, basique_from, bq_dkim, dkim_d))
            _add_manual_smtp({**s, "label": f"BASIQUE_INBOX{dkim_tag}_{s['label']}"})
        elif bq == "spam":
            basique_spam.append((s, basique_from, bq_dkim, dkim_d))
            _add_manual_smtp({**s, "label": f"BASIQUE_SPAM{dkim_tag}_{s['label']}"})
        else:
            dead_list.append((s, basique_from, False, ""))'''

if old_classify in content:
    content = content.replace(old_classify, new_classify)
    print("[OK] CLASSIFY section updated with DKIM tags")
else:
    print("[WARN] CLASSIFY section not found exactly")

# ============================================================
# Store markers in smtp_results for DKIM lookup
# ============================================================
old_marker_spoof = '''    for marker, s in stats["markers_spoof"].items():
        key = f"{s['host']}:{s['port']}|{s['user']}"
        if key not in smtp_results:
            smtp_results[key] = {"smtp": s, "spoof": "not_found", "basique": "not_found"}
        smtp_results[key]["spoof"] = imap_results.get(marker, "not_found")

    for marker, s in stats["markers_basique"].items():
        key = f"{s['host']}:{s['port']}|{s['user']}"
        if key not in smtp_results:
            smtp_results[key] = {"smtp": s, "spoof": "not_found", "basique": "not_found"}
        smtp_results[key]["basique"] = imap_results.get(marker, "not_found")'''

new_marker_spoof = '''    for marker, s in stats["markers_spoof"].items():
        key = f"{s['host']}:{s['port']}|{s['user']}"
        if key not in smtp_results:
            smtp_results[key] = {"smtp": s, "spoof": "not_found", "basique": "not_found", "spoof_marker": "", "basique_marker": ""}
        smtp_results[key]["spoof"] = imap_results.get(marker, "not_found")
        smtp_results[key]["spoof_marker"] = marker

    for marker, s in stats["markers_basique"].items():
        key = f"{s['host']}:{s['port']}|{s['user']}"
        if key not in smtp_results:
            smtp_results[key] = {"smtp": s, "spoof": "not_found", "basique": "not_found", "spoof_marker": "", "basique_marker": ""}
        smtp_results[key]["basique"] = imap_results.get(marker, "not_found")
        smtp_results[key]["basique_marker"] = marker'''

if old_marker_spoof in content:
    content = content.replace(old_marker_spoof, new_marker_spoof)
    print("[OK] Marker storage for DKIM lookup added")
else:
    print("[WARN] Marker storage section not found")

# ============================================================
# Update report section to show DKIM info
# ============================================================
# Find the report section after classification
old_report_spoof_inbox = '    if spoof_inbox:\n        report.append("\\nSPOOF INBOX:")\n        for s, bf in spoof_inbox:'
new_report_spoof_inbox = '    if spoof_inbox:\n        nodkim_count = sum(1 for _, _, dk, _ in spoof_inbox if not dk)\n        dkim_count = sum(1 for _, _, dk, _ in spoof_inbox if dk)\n        report.append(f"\\nSPOOF INBOX: {len(spoof_inbox)} (NO DKIM: {nodkim_count} | DKIM: {dkim_count})")\n        for s, bf, has_dkim, dkim_d in spoof_inbox:'

if old_report_spoof_inbox in content:
    content = content.replace(old_report_spoof_inbox, new_report_spoof_inbox)
    print("[OK] Report SPOOF INBOX updated with DKIM info")

    # Update the line that prints each SMTP in spoof_inbox
    old_line = '            report.append(f"  {s[\'host\']}:{s[\'port\']}|{s[\'user\']}  [{prov}]")\n'
    # We need to find this line in the spoof_inbox block context
else:
    print("[WARN] Report SPOOF INBOX not found")

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(content)

print("\n[DONE] DKIM detection patch applied")
