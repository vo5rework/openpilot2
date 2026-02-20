#!/usr/bin/env python3
"""
/data/openpilot/tools/xnor_patch_card_0x659_carrier.py

Patches selfdrive/car/card.py to publish 0x659 via the existing sendcan publisher (card.py),
even when carControl isn't alive.

- Imports Tesla659Carrier
- Instantiates it after CP is available
- Publishes its frames right after publishing carState (so it runs continuously)

Backup: card.py.bak_0x659_card
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CARD = Path("/data/openpilot/selfdrive/car/card.py")
IMPORT = "from selfdrive.tesla_0x659 import Tesla659Carrier"
MARKER = "UNITY_PARITY_0x659_FROM_CARD"

RE_AFTER_CP = re.compile(r"^\s*self\.CP\.alternativeExperience\s*=\s*0\s*$", re.M)
RE_AFTER_CARSTATE_SEND = re.compile(r"^\s*self\.pm\.send\('carState',\s*cs_send\)\s*$", re.M)

INJECT_INIT = f"""
    # {MARKER}: publish 0x659 carrier from card.py (single sendcan publisher)
    self._tesla_659 = Tesla659Carrier() if getattr(self.CP, "carName", "") == "tesla" else None
""".strip("\n")

INJECT_PUBLISH = f"""
    # {MARKER}: 0x659 carrier runs even when carControl isn't alive
    if getattr(self, "_tesla_659", None) is not None:
      extra = self._tesla_659.tick(self.sm.frame, CS)
      if extra:
        self.pm.send('sendcan', can_list_to_can_capnp(extra, msgtype='sendcan', valid=CS.canValid))
""".strip("\n")


def main() -> int:
  if not CARD.exists():
    raise SystemExit(f"missing: {CARD}")

  old = CARD.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  if MARKER in old:
    print("OK: already patched")
    return 0

  new = old

  # add import
  if IMPORT not in new:
    lines = new.splitlines(True)
    ins = 0
    for i, ln in enumerate(lines[:80]):
      if ln.startswith("import ") or ln.startswith("from "):
        ins = i + 1
    lines.insert(ins, IMPORT + "\n")
    new = "".join(lines)

  # inject init after CP is set (right before alternativeExperience line)
  m = RE_AFTER_CP.search(new)
  if not m:
    raise RuntimeError("card.py: couldn't find 'self.CP.alternativeExperience = 0' anchor")
  ind = re.match(r"^(\s*)", new[m.start():]).group(1)
  init_block = "\n" + "\n".join(ind + ln for ln in INJECT_INIT.splitlines()) + "\n"
  new = new[:m.start()] + init_block + new[m.start():]

  # inject publish right after sending carState
  m2 = RE_AFTER_CARSTATE_SEND.search(new)
  if not m2:
    raise RuntimeError("card.py: couldn't find carState publish anchor")
  ind2 = re.match(r"^(\s*)", new[m2.start():]).group(1)
  pub_block = "\n" + "\n".join(ind2 + ln for ln in INJECT_PUBLISH.splitlines()) + "\n"
  new = new[:m2.end()] + pub_block + new[m2.end():]

  ast.parse(new, filename=str(CARD))

  bak = CARD.with_suffix(".py.bak_0x659_card")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  CARD.write_text(new, encoding="utf-8")
  print(f"PATCHED: {CARD} (backup: {bak})")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())

