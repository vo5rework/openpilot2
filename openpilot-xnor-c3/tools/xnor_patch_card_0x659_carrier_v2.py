#!/usr/bin/env python3
"""
/data/openpilot/tools/xnor_patch_card_0x659_carrier_v2.py

Patch /data/openpilot/selfdrive/car/card.py:
- import Tesla659Carrier
- init self._tesla_659 + self._tesla_659_frame after CP is set
- publish 0x659 right after can_list is created in state_update()
  using the existing sendcan socket: self.pm.sock['sendcan'].send(...)

Idempotent marker: UNITY_PARITY_0x659_CARD_V2
Backup: card.py.bak_0x659_card_v2
"""

from __future__ import annotations
import ast
import re
from pathlib import Path

CARD = Path("/data/openpilot/selfdrive/car/card.py")
MARKER = "UNITY_PARITY_0x659_CARD_V2"
IMPORT = "from selfdrive.tesla_0x659 import Tesla659Carrier"

RE_INIT_ANCHOR = re.compile(r"^\s*self\.CP\.alternativeExperience\s*=\s*0\s*$", re.M)
RE_STATE_ANCHOR = re.compile(r"^\s*can_list\s*=\s*can_capnp_to_list\(can_strs\)\s*$", re.M)

def main() -> int:
  if not CARD.exists():
    raise SystemExit(f"missing: {CARD}")

  old = CARD.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  if MARKER in old:
    print("OK: already patched")
    return 0

  new = old

  # 1) Ensure import exists
  if IMPORT not in new:
    lines = new.splitlines(True)
    ins = 0
    for i, ln in enumerate(lines[:120]):
      if ln.startswith("import ") or ln.startswith("from "):
        ins = i + 1
    lines.insert(ins, IMPORT + "\n")
    new = "".join(lines)

  # 2) Init block after CP is set (anchor: alternativeExperience)
  m = RE_INIT_ANCHOR.search(new)
  if not m:
    raise RuntimeError("card.py: couldn't find init anchor: self.CP.alternativeExperience = 0")
  ind = re.match(r"^(\s*)", new[m.start():]).group(1)
  init_block = (
    f"\n{ind}# {MARKER}: 0x659 carrier state\n"
    f"{ind}self._tesla_659 = Tesla659Carrier() if getattr(self.CP, 'carName', '') == 'tesla' else None\n"
    f"{ind}self._tesla_659_frame = 0\n"
  )
  new = new[:m.start()] + init_block + new[m.start():]

  # 3) Publish block inside state_update right after can_list creation
  m2 = RE_STATE_ANCHOR.search(new)
  if not m2:
    raise RuntimeError("card.py: couldn't find state_update anchor: can_list = can_capnp_to_list(can_strs)")
  ind2 = re.match(r"^(\s*)", new[m2.start():]).group(1)
  pub_block = (
    f"\n{ind2}# {MARKER}: publish 0x659 via existing sendcan publisher\n"
    f"{ind2}if getattr(self, '_tesla_659', None) is not None:\n"
    f"{ind2}  self._tesla_659_frame += 1\n"
    f"{ind2}  extra = self._tesla_659.tick(self._tesla_659_frame, can_list)\n"
    f"{ind2}  if extra:\n"
    f"{ind2}    self.pm.sock['sendcan'].send(can_list_to_can_capnp(extra, msgtype='sendcan'))\n"
  )
  new = new[:m2.end()] + pub_block + new[m2.end():]

  ast.parse(new, filename=str(CARD))

  bak = CARD.with_suffix(".py.bak_0x659_card_v2")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  CARD.write_text(new, encoding="utf-8")
  print(f"PATCHED: {CARD} (backup: {bak})")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
