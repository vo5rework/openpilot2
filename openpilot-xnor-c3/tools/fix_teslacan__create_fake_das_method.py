#!/usr/bin/env python3
"""
/data/openpilot/tools/fix_teslacan__create_fake_das_method.py

Fix crash:
  AttributeError: 'TeslaCAN' object has no attribute '_create_fake_das'

Adds a compatibility method TeslaCAN._create_fake_das(...) that forwards to
module-level create_fake_das_msg(...).

Patches both trees if present:
  /data/openpilot/opendbc/car/tesla/teslacan.py
  /data/openpilot/opendbc_repo/opendbc/car/tesla/teslacan.py

Backup: *.bak__create_fake_das_method
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path("/data/openpilot")
PATHS = [
  ROOT / "opendbc/car/tesla/teslacan.py",
  ROOT / "opendbc_repo/opendbc/car/tesla/teslacan.py",
]

CLASS_RE = re.compile(r"^class\s+TeslaCAN\s*:\s*$", re.M)
DEF_RE = re.compile(r"^\s+def\s+_create_fake_das\s*\(", re.M)

METHOD_BLOCK = """
  def _create_fake_das(self, pedal_enabled: bool, autopilot_disabled: bool, bus: int,
                       stalk_main: bool = False, stalk_cancel: bool = False):
    \"\"\"Compatibility wrapper expected by some Tesla controller forks.\"\"\"
    return create_fake_das_msg(pedal_enabled, autopilot_disabled, bus,
                               stalk_main=stalk_main, stalk_cancel=stalk_cancel)
""".strip("\n") + "\n"


def patch_one(p: Path) -> bool:
  if not p.exists():
    return False
  old = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

  if DEF_RE.search(old):
    print(f"OK already has _create_fake_das: {p}")
    return True

  m = CLASS_RE.search(old)
  if not m:
    raise RuntimeError(f"{p}: couldn't find 'class TeslaCAN:'")

  # Insert right after class header line
  insert_at = m.end()
  new = old[:insert_at] + "\n" + METHOD_BLOCK + old[insert_at:]

  bak = p.with_suffix(p.suffix + ".bak__create_fake_das_method")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")
  print(f"PATCHED: {p} (backup: {bak})")
  return True


def main() -> int:
  any_found = any(p.exists() for p in PATHS)
  if not any_found:
    raise SystemExit("No teslacan.py found in expected locations")
  for p in PATHS:
    patch_one(p)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
