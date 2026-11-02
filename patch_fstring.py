"""Fix: f-string syntax errors in proxy retry messages."""

with open("auditguard/sender_bot.py", "r") as f:
    content = f.read()

# Fix template spoof proxy retry message (broken f-string)
content = content.replace(
    '''                f"Proxy erreur ({result['error'][:60]})\nRetry SANS proxy..."''',
    '''                "Proxy erreur (" + result.get("error", "")[:60] + ")\\nRetry SANS proxy..."'''
)

# Also fix if it got split across lines  
content = content.replace(
    """                f"Proxy erreur ({result['error'][:60]})\nRetry SANS proxy...\"""",
    '''                "Proxy erreur (" + result.get("error", "")[:60] + ")\\nRetry SANS proxy..."'''
)

# Fix the broken multiline version
old_broken = '''                f"Proxy erreur ({result['error'][:60]})
Retry SANS proxy..."'''
new_fixed = '''                "Proxy erreur (" + result.get("error", "")[:60] + ")\\nRetry SANS proxy..."'''

content = content.replace(old_broken, new_fixed)

# Also check/fix the spoof test (non-template) version
old_spoof_broken = '''                f"Proxy erreur ({result['error'][:60]})
Retry SANS proxy..."'''
# Already handled above

# Write
with open("auditguard/sender_bot.py", "w") as f:
    f.write(content)

print("FIX COMPLETE")
