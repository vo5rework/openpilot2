#!/usr/bin/env python3
"""
/data/openpilot/tools/revert_bad_cp_snippet_in_carstate.py

Removes the bad snippet that references `cp` in Tesla carstate, which crashes card:
  NameError: name 'cp' is not defined

Looks for marker:
  UNITY_PARITY_CRUISE_BUTTONS_FROM_STALK_V2

Patches both:
  /data/openpilot/opendbc/car/tesla/carstate.py
  /data/openpilot/opendbc_repo/opendbc/car/tesla/carstate.py

Backup: *.bak_revert_cp_snippet
"""
from __future__ import annotations
import re
from pathlib import Path

TARGETS = [
  Path("/data/openpilot/opendbc/car/tesla/carstate.py"),
  Path("/data/openpilot/opendbc_repo/opendbc/car/tesla/carstate.py"),
]

MARK = "UNITY_PARITY_CRUISE_BUTTONS_FROM_STALK_V2"

# Remove the whole marker block up to (but not including) the next "return ret"
BLOCK_RE = re.compile(
  r"(?ms)^[ \t]*#\s*" + re.escape(MARK) + r".*?\n(?=^[ \t]*return\s+ret\s*$)"
)

def patch(p: Path) -> bool:
  if not p.exists():
    return False
  s = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  if MARK not in s:
    print(f"OK: no {MARK} in {p}")
    return True
  s2, n = BLOCK_RE.subn("", s, count=1)
  if n != 1:
    raise RuntimeError(f"{p}: found marker but couldn't remove block safely (matches={n})")
  bak = p.with_suffix(p.suffix + ".bak_revert_cp_snippet")
  if not bak.exists():
    bak.write_text(s, encoding="utf-8")
  p.write_text(s2, encoding="utf-8")
  print(f"PATCHED: removed bad cp snippet in {p} (backup: {bak})")
  return True

def main() -> int:
  any_ = False
  for p in TARGETS:
    if p.exists():
      patch(p); any_ = True
  if not any_:
    raise SystemExit("No tesla carstate.py found")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
