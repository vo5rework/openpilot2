#!/usr/bin/env python3
"""
/data/openpilot/tools/xnor_patch_card_0x659_carrier_v3.py

Patch /data/openpilot/selfdrive/car/card.py (XNOR) to publish 0x659 from card.py.

Anchors (present in your file):
- init after:  self.rk = Ratekeeper(100, print_delay_threshold=None)
- update_from_can after:  CS = self.CI.update(can_list)
- publish after:  self.pm.send('carState', cs_send)

Backups: card.py.bak_0x659_card_v3
Idempotent marker.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CARD = Path("/data/openpilot/selfdrive/car/card.py")
IMPORT = "from openpilot.selfdrive.tesla_0x659 import Tesla659Carrier"
MARKER = "UNITY_PARITY_0x659_FROM_CARD_V3"

RE_RK = re.compile(r"^(?P<ind>\s*)self\.rk\s*=\s*Ratekeeper\(\s*100\s*,\s*print_delay_threshold=None\s*\)\s*$", re.M)
RE_CI_UPDATE = re.compile(r"^(?P<ind>\s*)CS\s*=\s*self\.CI\.update\(\s*can_list\s*\)\s*$", re.M)
RE_SEND_CARSTATE = re.compile(r"^(?P<ind>\s*)self\.pm\.send\(\s*'carState'\s*,\s*cs_send\s*\)\s*$", re.M)

INIT_BLOCK = f"""
# {MARKER}: Tesla 0x659 carrier (single sendcan publisher)
self._tesla_659 = Tesla659Carrier() if getattr(self.CP, "carName", "") == "tesla" else None
""".strip("\n")

UPDATE_BLOCK = f"""
# {MARKER}: edge-detect stalk from raw CAN before publishing carState
if getattr(self, "_tesla_659", None) is not None:
  self._tesla_659.update_from_can(can_list)
""".strip("\n")

PUBLISH_BLOCK = f"""
# {MARKER}: publish 0x659 even when carControl isn't alive
if getattr(self, "_tesla_659", None) is not None:
  extra = self._tesla_659.build_msgs(self.sm.frame)
  if extra:
    self.pm.send('sendcan', can_list_to_can_capnp(extra, msgtype='sendcan', valid=CS.canValid))
""".strip("\n")


def _norm(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")


def _insert_import(src: str) -> str:
  if IMPORT in src:
    return src
  lines = src.splitlines(True)
  ins = 0
  for i, ln in enumerate(lines[:140]):
    if ln.startswith("import ") or ln.startswith("from "):
      ins = i + 1
  lines.insert(ins, IMPORT + "\n")
  return "".join(lines)


def _indent_block(ind: str, block: str) -> str:
  return "\n" + "\n".join((ind + ln) if ln else "" for ln in block.splitlines()) + "\n"


def _inject_after(src: str, regex: re.Pattern[str], block: str) -> str:
  m = regex.search(src)
  if not m:
    raise RuntimeError(f"anchor not found: {regex.pattern}")
  ind = m.group("ind")
  pos = m.end()
  return src[:pos] + _indent_block(ind, block) + src[pos:]


def main() -> int:
  if not CARD.exists():
    raise SystemExit(f"missing: {CARD}")

  old = _norm(CARD.read_text(encoding="utf-8", errors="replace"))
  if MARKER in old:
    print("OK: already patched")
    return 0

  # ensure current file parses
  ast.parse(old, filename=str(CARD))

  new = _insert_import(old)
  new = _inject_after(new, RE_RK, INIT_BLOCK)
  new = _inject_after(new, RE_CI_UPDATE, UPDATE_BLOCK)
  new = _inject_after(new, RE_SEND_CARSTATE, PUBLISH_BLOCK)

  # validate patched
  ast.parse(new, filename=str(CARD))

  bak = CARD.with_suffix(".py.bak_0x659_card_v3")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  CARD.write_text(new, encoding="utf-8")
  print(f"PATCHED: {CARD} (backup: {bak})")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
