#!/usr/bin/env python3
"""
Repair opendbc Tesla teslacan exports so openpilot boots.

Ensures in /data/openpilot/opendbc/car/tesla/teslacan.py:
- tesla_checksum exists (required by opendbc.can.dbc)
- create_fake_das_msg exists (required by your carcontroller.py import)

Idempotent, creates a .bak backup once, validates syntax via ast.
"""

from __future__ import annotations

import ast
from pathlib import Path


TESLACAN = Path("/data/openpilot/opendbc/car/tesla/teslacan.py")

TESLA_CHECKSUM_BLOCK = """
def tesla_checksum(address: int, sig, d: bytearray) -> int:
  \"\"\"Checksum used by opendbc dbc packer for Tesla frames.\"\"\"
  checksum = (address & 0xFF) + ((address >> 8) & 0xFF)
  checksum_byte = sig.start_bit // 8
  for i in range(len(d)):
    if i != checksum_byte:
      checksum += d[i]
  return checksum & 0xFF
"""

CREATE_FAKE_DAS_BLOCK = """
def create_fake_das_msg(pedal_enabled: bool, autopilot_disabled: bool, bus: int,
                        stalk_main: bool = False, stalk_cancel: bool = False):
  \"\"\"Internal openpilot->panda carrier (0x659). Safety consumes + blocks it from the car.

  Byte5 bits:
    bit7: autopilot_disabled
    bit5: pedal_enabled
    bit1: stalk_main (edge)
    bit0: stalk_cancel (edge)
  \"\"\"
  # If your fork already has a differently-named helper, reuse it.
  if "create_fake_das_message" in globals():
    return globals()["create_fake_das_message"](pedal_enabled, autopilot_disabled, stalk_main=stalk_main, stalk_cancel=stalk_cancel, bus=bus)

  dat = bytearray(8)
  dat[5] = ((0x20 if pedal_enabled else 0) |
            (0x80 if autopilot_disabled else 0) |
            (0x02 if stalk_main else 0) |
            (0x01 if stalk_cancel else 0))
  return (0x659, bytes(dat), bus)
"""


def main() -> int:
  if not TESLACAN.exists():
    raise SystemExit(f"missing: {TESLACAN}")

  old = TESLACAN.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  new = old

  if "def tesla_checksum" not in new:
    new = new.rstrip("\n") + "\n\n" + TESLA_CHECKSUM_BLOCK.strip("\n") + "\n"

  if "def create_fake_das_msg" not in new:
    new = new.rstrip("\n") + "\n\n" + CREATE_FAKE_DAS_BLOCK.strip("\n") + "\n"

  # Syntax validation (no imports)
  ast.parse(new, filename=str(TESLACAN))

  if new != old:
    bak = TESLACAN.with_suffix(".py.bak_exports_fix")
    if not bak.exists():
      bak.write_text(old, encoding="utf-8")
    TESLACAN.write_text(new, encoding="utf-8")
    print(f"PATCHED: {TESLACAN} (backup: {bak})")
  else:
    print(f"OK: no changes needed: {TESLACAN}")

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
