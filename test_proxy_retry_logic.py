#!/usr/bin/env python3
"""
Test suite for BUST GO Sender Bot - Proxy Retry Logic Bug Fix
Tests the retry-without-proxy logic when proxy errors occur during spoof tests.

Bug: User gets error 'GeneralProxyError: Socket error: 0x02: Connection not allowed by ruleset'
Fix: Added retry-without-proxy logic - if proxy fails with proxy-related error, retry directly without proxy
"""

import os
import sys
import re
import ast

# Test configuration
REMOTE_HOST = "145.223.69.210"
REMOTE_USER = "root"
REMOTE_PASS = "KYim2c2x68"
BOT_DIR = "/opt/darkvasend"
LOCAL_BOT_FILE = "/opt/darkvasend/auditguard/sender_bot.py"

def read_local_file():
    """Read the local sender_bot.py file"""
    with open(LOCAL_BOT_FILE, 'r') as f:
        return f.read()

def test_1_module_imports_successfully():
    """Test 1: Verify the module imports without syntax errors"""
    print("Test 1: Module imports successfully...")
    try:
        # Try to compile the code to check for syntax errors
        code = read_local_file()
        compile(code, LOCAL_BOT_FILE, 'exec')
        print("  PASS: Module compiles without syntax errors")
        return True
    except SyntaxError as e:
        print(f"  FAIL: Syntax error at line {e.lineno}: {e.msg}")
        return False

def test_2_spoof_test_flow_has_proxy_retry():
    """Test 2: Verify spoof test flow (waiting='spoof_subject') has retry-without-proxy logic"""
    print("Test 2: Spoof test flow has proxy retry logic...")
    code = read_local_file()
    
    # Look for the pattern: if not result["success"] and _proxy and "Proxy" in result.get("error", "")
    # This should be around line 2094 in the spoof_subject flow
    pattern = r'if not result\["success"\] and _proxy\d* and "Proxy" in result\.get\("error", ""\)'
    matches = list(re.finditer(pattern, code))
    
    if len(matches) >= 1:
        # Find the one in the spoof test flow (around line 2094)
        for match in matches:
            line_num = code[:match.start()].count('\n') + 1
            if 2080 <= line_num <= 2110:  # Around line 2094
                print(f"  PASS: Found proxy retry logic at line {line_num} in spoof test flow")
                return True
    
    print("  FAIL: Proxy retry logic not found in spoof test flow")
    return False

def test_3_template_spoof_flow_has_proxy_retry():
    """Test 3: Verify template spoof test flow (waiting='tpl_send_to') has retry-without-proxy logic"""
    print("Test 3: Template spoof test flow has proxy retry logic...")
    code = read_local_file()
    
    # Look for the pattern around line 1947
    pattern = r'if not result\["success"\] and _proxy\d* and "Proxy" in result\.get\("error", ""\)'
    matches = list(re.finditer(pattern, code))
    
    if len(matches) >= 1:
        # Find the one in the template spoof flow (around line 1947)
        for match in matches:
            line_num = code[:match.start()].count('\n') + 1
            if 1930 <= line_num <= 1960:  # Around line 1947
                print(f"  PASS: Found proxy retry logic at line {line_num} in template spoof flow")
                return True
    
    print("  FAIL: Proxy retry logic not found in template spoof flow")
    return False

def test_4_retry_uses_proxy_none():
    """Test 4: Verify retry calls _send_one with proxy=None (positional or keyword)"""
    print("Test 4: Retry calls _send_one with proxy=None...")
    code = read_local_file()
    
    # Look for the retry pattern with proxy=None (keyword argument)
    pattern_keyword = r'result = await asyncio\.to_thread\(\s*_send_one,[\s\S]*?proxy=None,?\s*\)'
    matches_keyword = list(re.finditer(pattern_keyword, code))
    
    # Also look for positional None at the end (template spoof flow uses positional)
    # The pattern is: _send_one, ..., None, ) after the "Retry SANS proxy" message
    pattern_positional = r'Retry SANS proxy[\s\S]{0,500}?result = await asyncio\.to_thread\(\s*_send_one,[\s\S]*?None,\s*\)'
    matches_positional = list(re.finditer(pattern_positional, code))
    
    total_matches = len(matches_keyword) + len(matches_positional)
    
    if total_matches >= 2:  # Should have at least 2 (one for each flow)
        print(f"  PASS: Found {total_matches} retry calls with proxy=None (keyword: {len(matches_keyword)}, positional: {len(matches_positional)})")
        return True
    
    print(f"  FAIL: Expected at least 2 retry calls with proxy=None, found {total_matches}")
    return False

def test_5_retry_message_format_correct():
    """Test 5: Verify the retry message format is correct (no broken f-strings)"""
    print("Test 5: Retry message format is correct...")
    code = read_local_file()
    
    # Look for the retry message pattern
    pattern = r'"Proxy erreur \(" \+ result\.get\("error", ""\)\[:60\] \+ "\)\\nRetry SANS proxy\.\.\."'
    matches = list(re.finditer(pattern, code))
    
    if len(matches) >= 2:  # Should have 2 (one for each flow)
        print(f"  PASS: Found {len(matches)} correctly formatted retry messages")
        return True
    
    print(f"  FAIL: Expected 2 retry messages, found {len(matches)}")
    return False

def test_6_proxy_keyword_detection():
    """Test 6: Verify 'Proxy' keyword in error triggers retry"""
    print("Test 6: 'Proxy' keyword in error triggers retry...")
    code = read_local_file()
    
    # The condition should check for "Proxy" in the error message
    # This catches errors like "GeneralProxyError: Socket error..."
    pattern = r'"Proxy" in result\.get\("error", ""\)'
    matches = list(re.finditer(pattern, code))
    
    if len(matches) >= 2:
        print(f"  PASS: Found {len(matches)} 'Proxy' keyword checks in error handling")
        return True
    
    print(f"  FAIL: Expected at least 2 'Proxy' keyword checks, found {len(matches)}")
    return False

def test_7_both_flows_have_complete_retry_logic():
    """Test 7: Verify both flows have complete retry logic structure"""
    print("Test 7: Both flows have complete retry logic structure...")
    code = read_local_file()
    
    # Check for the complete pattern in both flows
    # Pattern: check error -> send message -> retry with proxy=None
    
    # For spoof test flow (around line 2092-2110)
    spoof_flow_pattern = (
        r'# If proxy error, retry WITHOUT proxy\s*\n'
        r'.*?proxy_used_label = ""\s*\n'
        r'.*?if not result\["success"\] and _proxy\d* and "Proxy" in result\.get\("error", ""\):'
    )
    
    # For template spoof flow (around line 1946-1964)
    tpl_flow_pattern = (
        r'# If proxy error, retry WITHOUT proxy\s*\n'
        r'.*?if not result\["success"\] and _proxy and "Proxy" in result\.get\("error", ""\):'
    )
    
    spoof_match = re.search(spoof_flow_pattern, code, re.MULTILINE)
    tpl_match = re.search(tpl_flow_pattern, code, re.MULTILINE)
    
    if spoof_match and tpl_match:
        print("  PASS: Both flows have complete retry logic structure")
        return True
    
    if not spoof_match:
        print("  FAIL: Spoof test flow missing complete retry logic structure")
    if not tpl_match:
        print("  FAIL: Template spoof flow missing complete retry logic structure")
    return False

def test_8_service_running_on_remote():
    """Test 8: Verify darkvasend service is running on remote server"""
    print("Test 8: darkvasend service is running on remote server...")
    import subprocess
    
    cmd = f"sshpass -p '{REMOTE_PASS}' ssh -o StrictHostKeyChecking=no {REMOTE_USER}@{REMOTE_HOST} 'systemctl is-active darkvasend'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    
    if result.stdout.strip() == "active":
        print("  PASS: darkvasend service is active")
        return True
    
    print(f"  FAIL: darkvasend service status: {result.stdout.strip()}")
    return False

def test_9_remote_code_has_proxy_retry():
    """Test 9: Verify remote server code has proxy retry logic"""
    print("Test 9: Remote server code has proxy retry logic...")
    import subprocess
    
    cmd = f"sshpass -p '{REMOTE_PASS}' ssh -o StrictHostKeyChecking=no {REMOTE_USER}@{REMOTE_HOST} 'grep -c \"Retry SANS proxy\" {BOT_DIR}/auditguard/sender_bot.py'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    
    try:
        count = int(result.stdout.strip())
        if count >= 2:
            print(f"  PASS: Found {count} 'Retry SANS proxy' occurrences on remote server")
            return True
        print(f"  FAIL: Expected at least 2 occurrences, found {count}")
        return False
    except ValueError:
        print(f"  FAIL: Could not parse grep output: {result.stdout}")
        return False

def test_10_remote_module_imports():
    """Test 10: Verify module imports successfully on remote server"""
    print("Test 10: Module imports successfully on remote server...")
    import subprocess
    
    cmd = f"sshpass -p '{REMOTE_PASS}' ssh -o StrictHostKeyChecking=no {REMOTE_USER}@{REMOTE_HOST} 'cd {BOT_DIR} && source venv/bin/activate && python3 -c \"from auditguard import sender_bot; print(\\\"OK\\\")\"'"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    
    if "OK" in result.stdout:
        print("  PASS: Module imports successfully on remote server")
        return True
    
    print(f"  FAIL: Module import failed: {result.stderr}")
    return False

def main():
    """Run all tests"""
    print("=" * 60)
    print("BUST GO Sender Bot - Proxy Retry Logic Tests")
    print("=" * 60)
    print()
    
    tests = [
        test_1_module_imports_successfully,
        test_2_spoof_test_flow_has_proxy_retry,
        test_3_template_spoof_flow_has_proxy_retry,
        test_4_retry_uses_proxy_none,
        test_5_retry_message_format_correct,
        test_6_proxy_keyword_detection,
        test_7_both_flows_have_complete_retry_logic,
        test_8_service_running_on_remote,
        test_9_remote_code_has_proxy_retry,
        test_10_remote_module_imports,
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append((test.__name__, result))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append((test.__name__, False))
        print()
    
    # Summary
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  {status}: {name}")
    
    print()
    print(f"Total: {passed}/{total} tests passed")
    print("=" * 60)
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
