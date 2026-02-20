#!/usr/bin/env python3
# /data/openpilot/tools/xnor_fix_tesla_legacy_stalk_bus2.py

from __future__ import annotations

import re
from pathlib import Path

TARGETS = [
  Path("/data/openpilot/opendbc_repo/opendbc/safety/modes/tesla_legacy.h"),
  Path("/data/openpilot/opendbc_repo/opendbc/safety/modes/tesla.h"),
]

REPLS = [
  # (addr == 0x45) && (bus == 0)  -> (addr == 0x45) && ((bus == 0) || (bus == 2))
  (
    re.compile(r"\(\s*addr\s*==\s*0x45\s*\)\s*&&\s*\(\s*bus\s*==\s*0\s*\)"),
    r"(addr == 0x45) && ((bus == 0) || (bus == 2))",
  ),
  # bus == 0 && addr == 0x45  -> (bus == 0 || bus == 2) && addr == 0x45
  (
    re.compile(r"\(\s*bus\s*==\s*0\s*\)\s*&&\s*\(\s*addr\s*==\s*0x45\s*\)"),
    r"((bus == 0) || (bus == 2)) && (addr == 0x45)",
  ),
]

def patch(path: Path) -> bool:
  if not path.exists():
    return False
  s = path.read_text(errors="ignore")
  orig = s

  for cre, rep in REPLS:
    s = cre.sub(rep, s)

  if s != orig:
    bak = path.with_suffix(path.suffix + ".bak_stalkbus2")
    if not bak.exists():
      bak.write_text(orig)
    path.write_text(s)
    print(f"PATCHED: {path} (backup: {bak})")
    return True
  print(f"OK: no changes needed: {path}")
  return False

def main() -> int:
  changed = False
  for p in TARGETS:
    changed |= patch(p)
  if not changed:
    print("No files changed.")
  print("\nNext: rebuild/flash panda firmware after safety change.")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
