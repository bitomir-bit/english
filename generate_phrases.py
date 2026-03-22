#!/usr/bin/env python3
"""Converts phrases.md → phrases.json. Run by cron daily."""
import re, json, os

BASE = os.path.dirname(os.path.abspath(__file__))
phrases = []
current_section = "General"

with open(os.path.join(BASE, "phrases.md")) as f:
    for line in f:
        line = line.strip()
        if line.startswith("## "):
            current_section = line[3:].strip()
            continue
        m = re.match(r"\*\*(.+?)\*\*\s*[—-]\s*(.+)", line)
        if m:
            phrase = m.group(1).strip()
            rest = m.group(2).strip()
            ex_match = re.search(r"\*(.+?)\*\s*$", rest)
            if ex_match:
                meaning = rest[:ex_match.start()].strip().rstrip(".")
                example = ex_match.group(1).strip()
            else:
                meaning = rest
                example = ""
            phrases.append({
                "id": len(phrases),
                "phrase": phrase,
                "meaning": meaning,
                "example": example,
                "section": current_section
            })

out = os.path.join(BASE, "phrases.json")
with open(out, "w") as f:
    json.dump(phrases, f, indent=2, ensure_ascii=False)

print(f"✓ Generated {len(phrases)} phrases → phrases.json")
