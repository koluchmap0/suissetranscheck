"""Patch: Increase Telegram bot timeouts to avoid network timeouts on slow VPS."""

with open("auditguard/sender_bot.py", "r") as f:
    content = f.read()

# Replace Application builder with timeout config
old_builder = '    app = Application.builder().token(token).build()'

new_builder = '''    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=10.0,
    )
    app = Application.builder().token(token).request(request).build()'''

if old_builder in content:
    content = content.replace(old_builder, new_builder, 1)
    print("OK - Telegram timeouts set to 30s")
else:
    print("ERROR - Application builder not found")

with open("auditguard/sender_bot.py", "w") as f:
    f.write(content)

print("PATCH COMPLETE")
