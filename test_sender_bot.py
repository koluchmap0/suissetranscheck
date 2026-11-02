#!/usr/bin/env python3
"""
BUST GO Sender Bot - Comprehensive Test Suite
Tests for found_creds table, SMTP functions, and category classification.
"""
import sys
sys.path.insert(0, ".")
import sqlite3

def run_tests():
    print("=" * 60)
    print("BUST GO SENDER BOT - COMPREHENSIVE TEST SUITE")
    print("=" * 60)
    
    results = []
    
    # Test 1: found_creds table exists
    print("\n[TEST 1] found_creds table exists")
    db = sqlite3.connect("bustgo-hunt.db", timeout=10)
    db.row_factory = sqlite3.Row
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='found_creds'")
    exists = cursor.fetchone() is not None
    results.append(("Table exists", exists))
    print(f"  RESULT: {'PASS' if exists else 'FAIL'} - Table exists: {exists}")
    
    # Test 2: found_creds has required columns
    print("\n[TEST 2] found_creds has required columns")
    cursor = db.execute("PRAGMA table_info(found_creds)")
    columns = [row[1] for row in cursor.fetchall()]
    required = ["id", "domain", "provider", "context", "smtp_host", "smtp_port", "smtp_user", "smtp_password", "auth_ok"]
    missing = [c for c in required if c not in columns]
    test2_pass = not missing
    results.append(("Required columns", test2_pass))
    print(f"  RESULT: {'PASS' if test2_pass else 'FAIL'} - Missing columns: {missing if missing else 'None'}")
    
    db.close()
    
    # Test 3: _add_manual_smtp function
    print("\n[TEST 3] _add_manual_smtp inserts record correctly")
    from auditguard.sender_bot import _add_manual_smtp, _get_all_smtp, _classify_smtp, _kb_smtp_list
    
    test_smtp = {
        "host": "test.smtp.example.com",
        "port": 587,
        "user": "testuser@example.com",
        "password": "testpass123",
        "label": "SPOOF_PYTEST"
    }
    cred_id = _add_manual_smtp(test_smtp)
    db = sqlite3.connect("bustgo-hunt.db", timeout=10)
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM found_creds WHERE id=?", (cred_id,)).fetchone()
    test3_pass = row and row["context"] == "Manual: SPOOF_PYTEST" and row["auth_ok"] == 1
    results.append(("_add_manual_smtp", test3_pass))
    ctx_val = row["context"] if row else "N/A"
    auth_val = row["auth_ok"] if row else "N/A"
    print(f"  RESULT: {'PASS' if test3_pass else 'FAIL'} - ID={cred_id}, context={ctx_val}, auth_ok={auth_val}")
    
    # Test 4: _get_all_smtp returns context
    print("\n[TEST 4] _get_all_smtp returns context field")
    all_smtp = _get_all_smtp()
    found = [s for s in all_smtp if s["id"] == cred_id]
    ctx_result = found[0].get("context") if found else "NOT FOUND"
    test4_pass = found and found[0].get("context") == "Manual: SPOOF_PYTEST"
    results.append(("_get_all_smtp context", test4_pass))
    print(f"  RESULT: {'PASS' if test4_pass else 'FAIL'} - context in result: {ctx_result}")
    
    # Test 5: _classify_smtp SPOOF
    print("\n[TEST 5] _classify_smtp classifies SPOOF correctly")
    result = _classify_smtp({"context": "Manual: SPOOF_TEST"})
    test5_pass = result == "SPOOF"
    results.append(("_classify_smtp SPOOF", test5_pass))
    print(f"  RESULT: {'PASS' if test5_pass else 'FAIL'} - Classification: {result}")
    
    # Test 6: _classify_smtp BASIQUE
    print("\n[TEST 6] _classify_smtp classifies BASIQUE correctly")
    result = _classify_smtp({"context": "Manual: BASIQUE_TEST"})
    test6_pass = result == "BASIQUE"
    results.append(("_classify_smtp BASIQUE", test6_pass))
    print(f"  RESULT: {'PASS' if test6_pass else 'FAIL'} - Classification: {result}")
    
    # Test 7: _classify_smtp AUTRE
    print("\n[TEST 7] _classify_smtp classifies AUTRE correctly")
    result1 = _classify_smtp({"context": "Manual: OTHER_TEST"})
    result2 = _classify_smtp({"context": None})
    result3 = _classify_smtp({})
    test7_pass = result1 == "AUTRE" and result2 == "AUTRE" and result3 == "AUTRE"
    results.append(("_classify_smtp AUTRE", test7_pass))
    print(f"  RESULT: {'PASS' if test7_pass else 'FAIL'} - OTHER={result1}, None={result2}, Empty={result3}")
    
    # Test 8: _kb_smtp_list generates category headers
    print("\n[TEST 8] _kb_smtp_list generates category headers with noop")
    # Add entries for each category
    test_basique = {"host": "basique.test.com", "port": 587, "user": "basique@test.com", "password": "pass", "label": "BASIQUE_PYTEST"}
    test_autre = {"host": "autre.test.com", "port": 587, "user": "autre@test.com", "password": "pass", "label": "OTHER_PYTEST"}
    id_basique = _add_manual_smtp(test_basique)
    id_autre = _add_manual_smtp(test_autre)
    
    all_smtp = _get_all_smtp()
    kb = _kb_smtp_list(all_smtp)
    headers = []
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data == "noop":
                headers.append(btn.text)
    
    has_spoof = any("SPOOF" in h for h in headers)
    has_basique = any("BASIQUE" in h for h in headers)
    has_autre = any("AUTRE" in h for h in headers)
    test8_pass = has_spoof and has_basique and has_autre
    results.append(("_kb_smtp_list headers", test8_pass))
    print(f"  RESULT: {'PASS' if test8_pass else 'FAIL'} - Headers: {headers}")
    
    # Cleanup
    db.execute("DELETE FROM found_creds WHERE id IN (?,?,?)", (cred_id, id_basique, id_autre))
    db.commit()
    db.close()
    
    # Test 9: noop handler exists
    print("\n[TEST 9] noop handler exists in callbacks")
    import inspect
    from auditguard.sender_bot import callbacks
    source = inspect.getsource(callbacks)
    test9_pass = 'if data == "noop"' in source
    results.append(("noop handler", test9_pass))
    print(f"  RESULT: {'PASS' if test9_pass else 'FAIL'} - noop handler found: {test9_pass}")
    
    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, p in results if p)
    total = len(results)
    print(f"SUMMARY: {passed}/{total} tests passed")
    
    if passed == total:
        print("ALL TESTS PASSED!")
    else:
        print("FAILED TESTS:")
        for name, p in results:
            if not p:
                print(f"  - {name}")
    
    print("=" * 60)
    return passed == total

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
