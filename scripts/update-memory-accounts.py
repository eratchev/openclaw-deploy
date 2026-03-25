#!/usr/bin/env python3
"""Update the 'Available labels' line in workspace/MEMORY_GUIDE.md for a service.

Usage:
    python3 scripts/update-memory-accounts.py gmail personal,jobs
    python3 scripts/update-memory-accounts.py gcal  personal,jobs,work
"""
import sys
import re
import os

if len(sys.argv) != 3:
    print("Usage: update-memory-accounts.py <gmail|gcal> <label1,label2,...>", file=sys.stderr)
    sys.exit(1)

service = sys.argv[1]
labels = [l.strip() for l in sys.argv[2].split(",") if l.strip()]

if service not in ("gmail", "gcal"):
    print(f"Unknown service: {service}", file=sys.stderr)
    sys.exit(1)

if not labels:
    print("No labels provided — nothing to update", file=sys.stderr)
    sys.exit(1)

default = labels[0]
parts = [f"`{l}`" + (" (default)" if i == 0 else "") for i, l in enumerate(labels)]
label_str = ", ".join(parts)

# The gmail and gcal Available labels lines have distinct endings:
#   gcal:  ...to use the default.
#   gmail: ...to use the default (`personal`).
if service == "gmail":
    new_line = f"Available labels: {label_str}. Omit `--account` to use the default (`{default}`)."
    # Matches only the gmail variant (ends with (`label`).)
    pattern = r"Available labels:.*Omit `--account` to use the default \(`[^`]+`\)\."
else:
    new_line = f"Available labels: {label_str}. Omit `--account` to use the default."
    # Matches only the gcal variant (ends with just a period, no parenthetical)
    pattern = r"Available labels:.*Omit `--account` to use the default\."

repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = os.path.join(repo_dir, "workspace", "MEMORY_GUIDE.md")

content = open(path).read()
new_content, count = re.subn(pattern, new_line, content)
if count == 0:
    print(f"WARNING: No match found for {service} labels in MEMORY_GUIDE.md", file=sys.stderr)
    sys.exit(1)

open(path, "w").write(new_content)
print(f"Updated {service} labels → {label_str}")
