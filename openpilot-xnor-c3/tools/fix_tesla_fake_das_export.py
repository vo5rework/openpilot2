#!/usr/bin/env python3
"""
Fix boot ImportError:
- Ensure opendbc.car.tesla.teslacan exports create_fake_das_msg (and alias create_fake_das_message).
- Patch both trees if present:
    /data/openpilot/opendbc/...
    /data/openpilot/opendbc_repo/opendbc/...
- Insert at module scope before 'class TeslaCAN:' to avoid indentation/nesting mistakes.
- Backup once: *.bak_fake_das_export
- Syntax validate via ast.parse (no runtime imports)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path("/data/openpilot")

TESLACAN_PATHS = [
  ROOT / "opendbc/car/tesla/teslacan.py",
  ROOT / "opendbc_repo/opendbc/car/tesla/teslacan.py",
]

HELPER_BLOCK = """
def create_fake_das_msg(pedal_enabled: bool,
                        autopilot_disabled: bool,
                        bus: int,
                        stalk_main: bool = False,
                        stalk_cancel: bool = False):
  \"\"\"Internal openpilot->panda carrier (0x659). Safety consumes + blocks it from the car.

  Byte5 bits:
    bit7: autopilot_disabled
    bit5: pedal_enabled
    bit1: stalk_main (edge)
    bit0: stalk_cancel (edge)
  \"\"\"
  dat = bytearray(8)
  dat[5] = ((0x20 if pedal_enabled else 0) |
            (0x80 if autopilot_disabled else 0) |
            (0x02 if stalk_main else 0) |
            (0x01 if stalk_cancel else 0))
  return (0x659, bytes(dat), bus)


def create_fake_das_message(pedal_enabled: bool,
                            autopilot_disabled: bool,
                            *,
                            stalk_main: bool = False,
                            stalk_cancel: bool = False,
                            bus: int = 0):
  \"\"\"Compatibility alias for forks that expect create_fake_das_message().\"\"\"
  return create_fake_das_msg(pedal_enabled, autopilot_disabled, bus,
                             stalk_main=stalk_main, stalk_cancel=stalk_cancel)
"""

CLASS_RE = re.compile(r"^class\s+TeslaCAN\s*:", re.M)

def patch_one(p: Path) -> None:
  if not p.exists():
    return

  old = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  if "def create_fake_das_msg" in old:
    ast.parse(old, filename=str(p))
    print(f"OK: already has create_fake_das_msg: {p}")
    return

  m = CLASS_RE.search(old)
  if m:
    new = old[:m.start()] + HELPER_BLOCK.strip("\n") + "\n\n" + old[m.start():]
  else:
    new = old.rstrip("\n") + "\n\n" + HELPER_BLOCK.strip("\n") + "\n"

  ast.parse(new, filename=str(p))

  bak = p.with_suffix(p.suffix + ".bak_fake_das_export")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")
  print(f"PATCHED: {p} (backup: {bak})")

def main() -> int:
  any_found = False
  for p in TESLACAN_PATHS:
    if p.exists():
      any_found = True
    patch_one(p)
  if not any_found:
    raise SystemExit("No teslacan.py found in expected locations")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
