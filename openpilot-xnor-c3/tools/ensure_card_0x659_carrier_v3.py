# FILE: /data/openpilot/tools/ensure_card_0x659_carrier_v3.py
#!/usr/bin/env python3
"""
Ensure card.py publishes the 0x659 carrier from selfdrive/tesla_0x659.py.

Idempotent marker: UNITY_PARITY_0x659_CARD_V3
Backup: /data/openpilot/selfdrive/car/card.py.bak_0x659_card_v3

This runs inside the existing sendcan publisher (card.py), so no multi-publisher errors.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CARD = Path("/data/openpilot/selfdrive/car/card.py")
MARK = "UNITY_PARITY_0x659_CARD_V3"
IMPORT = "from selfdrive.tesla_0x659 import Tesla659Carrier"

RE_IMPORT_AREA = re.compile(r"^(?:import .*\n|from .*\n)+", re.M)
RE_INIT_ANCHOR = re.compile(r"^\s*self\.CP\.alternativeExperience\s*=\s*0\s*$", re.M)
RE_CANLIST_ANCHOR = re.compile(r"^\s*can_list\s*=\s*can_capnp_to_list\(can_strs\)\s*$", re.M)

def main() -> int:
  if not CARD.exists():
    raise SystemExit(f"missing: {CARD}")

  old = CARD.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  if MARK in old:
    print("OK: already patched")
    return 0

  new = old

  # import
  if IMPORT not in new:
    m = RE_IMPORT_AREA.search(new)
    if not m:
      raise RuntimeError("card.py: couldn't find import area")
    new = new[:m.end()] + IMPORT + "\n" + new[m.end():]

  # init carrier after CP is set (anchor is stable in your trace)
  m2 = RE_INIT_ANCHOR.search(new)
  if not m2:
    raise RuntimeError("card.py: couldn't find init anchor (self.CP.alternativeExperience = 0)")
  ind = re.match(r"^(\s*)", new[m2.start():]).group(1)
  init_block = (
    f"\n{ind}# {MARK}: 0x659 carrier\n"
    f"{ind}self._tesla_659 = Tesla659Carrier()\n"
  )
  new = new[:m2.start()] + init_block + new[m2.start():]

  # publish carrier right after can_list is created
  m3 = RE_CANLIST_ANCHOR.search(new)
  if not m3:
    raise RuntimeError("card.py: couldn't find can_list anchor (can_list = can_capnp_to_list(can_strs))")
  ind2 = re.match(r"^(\s*)", new[m3.start():]).group(1)
  pub_block = (
    f"\n{ind2}# {MARK}: publish 0x659 via existing sendcan publisher\n"
    f"{ind2}if getattr(self, '_tesla_659', None) is not None:\n"
    f"{ind2}  extra = self._tesla_659.tick(can_list)\n"
    f"{ind2}  if extra:\n"
    f"{ind2}    self.pm.sock['sendcan'].send(can_list_to_can_capnp(extra, msgtype='sendcan'))\n"
  )
  new = new[:m3.end()] + pub_block + new[m3.end():]

  ast.parse(new, filename=str(CARD))

  bak = CARD.with_suffix(".py.bak_0x659_card_v3")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  CARD.write_text(new, encoding="utf-8")
  print(f"PATCHED: {CARD} (backup: {bak})")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
