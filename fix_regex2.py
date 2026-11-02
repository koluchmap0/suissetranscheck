#!/usr/bin/env python3
"""Clean up broken regex lines in sender_bot.py."""

TARGET = "/opt/darkvasend/auditguard/sender_bot.py"

with open(TARGET, "r") as f:
    lines = f.readlines()

cleaned = []
for line in lines:
    stripped = line.strip()
    # Skip orphan broken continuation lines like: \s,;]+", text.strip())
    if stripped.startswith("\\s,;"):
        print(f"REMOVED: {stripped!r}")
        continue
    if stripped.startswith("\\r\\s,;"):
        print(f"REMOVED: {stripped!r}")
        continue
    cleaned.append(line)

with open(TARGET, "w") as f:
    f.writelines(cleaned)

print(f"Cleaned {len(lines) - len(cleaned)} broken lines")

# Now verify all re.split lines are correct
import ast
try:
    ast.parse(open(TARGET).read())
    print("SYNTAX OK")
except SyntaxError as e:
    print(f"SYNTAX ERROR at line {e.lineno}: {e.msg}")
    # Show context
    with open(TARGET) as f:
        all_lines = f.readlines()
    for i in range(max(0, e.lineno-3), min(len(all_lines), e.lineno+2)):
        print(f"  {i+1}: {all_lines[i].rstrip()}")
