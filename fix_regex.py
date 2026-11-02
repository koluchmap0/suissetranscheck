#!/usr/bin/env python3
"""Fix broken regex in sender_bot.py email parsers."""
import re

TARGET = "/opt/darkvasend/auditguard/sender_bot.py"

with open(TARGET, "r") as f:
    lines = f.readlines()

fixed_lines = []
skip_next = False
for i, line in enumerate(lines):
    if skip_next:
        skip_next = False
        continue
    
    # Detect the broken pattern: re.split(r"[ followed by newline
    if 'raw_parts = re.split(r"[' in line and line.rstrip().endswith('"['):
        # This line + next line = broken regex, replace with fixed version
        indent = line[:len(line) - len(line.lstrip())]
        fixed_lines.append(indent + 'raw_parts = re.split(r"[\\n\\r\\s,;]+", text.strip())\n')
        skip_next = True  # Skip the next line (it's the broken continuation)
        continue
    
    # Also fix if it's on one line but wrong
    if 'raw_parts = re.split(r"[\n' in repr(line):
        indent = line[:len(line) - len(line.lstrip())]
        fixed_lines.append(indent + 'raw_parts = re.split(r"[\\n\\r\\s,;]+", text.strip())\n')
        continue
    
    fixed_lines.append(line)

with open(TARGET, "w") as f:
    f.writelines(fixed_lines)

# Verify
import ast
ast.parse(open(TARGET).read())
print("FIX APPLIED + SYNTAX OK")
