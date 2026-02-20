#!/usr/bin/env python3
"""
/data/openpilot/tools/xnor_patch_cc_0x659_no_params.py

Unity-parity 0x659 carrier WITHOUT Params keys:
- pedal_enabled is hardcoded False (pre-AP feature removed)
- autopilot_disabled is hardcoded True (lateral-only contract)
- 0x659 published via existing can_sends path (single sendcan publisher)
- on bus 0 and bus 4, 10Hz + MAIN/CANCEL edges

Backups: *.bak_cc_0x659_noparams
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path("/data/openpilot")

CC_PATHS = [
  ROOT / "opendbc/car/tesla/carcontroller.py",
  ROOT / "opendbc_repo/opendbc/car/tesla/carcontroller.py",
]

IMPORT_LINE = "from opendbc.car.tesla.teslacan import create_fake_das_msg"
MARKER = "UNITY_PARITY_0x659_CARRIER_NOPARAMS"

RETURN_WITH_CANSENDS_RE = re.compile(r"^(?P<ind>\s+)return\b[^\n]*\bcan_sends\b[^\n]*$", re.M)
DEF_UPDATE_RE = re.compile(r"^(\s*)def\s+update\s*\(.*\)\s*:\s*$", re.M)

INJECT_BLOCK = f"""
# {MARKER}: internal carrier for panda safety (no multi-publisher daemon)
# pedal_enabled removed (pre-AP feature): always False
# autopilot_disabled forced True (lateral-only)
stalk_btn = int(getattr(CS, "cruise_buttons", 0))
prev_btn = int(getattr(self, "_prev_cruise_buttons", 0))
main_edge = (stalk_btn == 2) and (prev_btn != 2)
cancel_edge = (stalk_btn == 1) and (prev_btn != 1)
self._prev_cruise_buttons = stalk_btn

ap_disabled = True
pedal_en = False

if ((self.frame % 10) == 0) or main_edge or cancel_edge:
  for bus in (0, 4):
    can_sends.append(create_fake_das_msg(pedal_en, ap_disabled, bus,
                                         stalk_main=main_edge,
                                         stalk_cancel=cancel_edge))
"""

def _norm(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")

def _backup_write(p: Path, old: str, new: str) -> None:
  if new == old:
    return
  bak = p.with_suffix(p.suffix + ".bak_cc_0x659_noparams")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")

def _ensure_import(src: str) -> str:
  if IMPORT_LINE in src:
    return src
  lines = src.splitlines(True)
  ins = 0
  for i, ln in enumerate(lines[:60]):
    if ln.startswith("import ") or ln.startswith("from "):
      ins = i + 1
  lines.insert(ins, IMPORT_LINE + "\n")
  return "".join(lines)

def _ensure_init_fields(src: str) -> str:
  # ensure self._prev_cruise_buttons exists
  if "_prev_cruise_buttons" in src:
    return src
  return re.sub(r"(self\.frame\s*=\s*0\s*\n)", r"\1    self._prev_cruise_buttons = 0\n", src, count=1)

def _remove_old_injections(src: str) -> str:
  # remove any prior variants of our injection to avoid duplicates
  src = re.sub(r"^\s*#\s*UNITY_PARITY_0x659_CARRIER.*?$[\s\S]*?^\s*can_sends\.append\(create_fake_das_msg[\s\S]*?\)\s*$",
               "", src, flags=re.M)
  return src

def patch_one(p: Path) -> None:
  if not p.exists():
    return
  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  if MARKER in old:
    print(f"OK already patched: {p}")
    return

  new = old
  new = _remove_old_injections(new)
  new = _ensure_import(new)
  new = _ensure_init_fields(new)

  m_up = DEF_UPDATE_RE.search(new)
  if not m_up:
    raise RuntimeError(f"{p}: couldn't find def update(...)")

  update_indent = m_up.group(1)
  after = new[m_up.end():]
  m_next = re.search(rf"^{re.escape(update_indent)}def\s+\w+\s*\(", after, re.M)
  update_end = m_up.end() + (m_next.start() if m_next else len(after))
  update_block = new[m_up.start():update_end]

  returns = list(RETURN_WITH_CANSENDS_RE.finditer(update_block))
  if not returns:
    raise RuntimeError(f"{p}: couldn't find a return containing can_sends in update()")

  m_ret = returns[-1]
  ind = m_ret.group("ind")
  inject = "\n" + "\n".join(ind + ln if ln else "" for ln in INJECT_BLOCK.strip("\n").splitlines()) + "\n"
  update_block2 = update_block[:m_ret.start()] + inject + update_block[m_ret.start():]

  new2 = new[:m_up.start()] + update_block2 + new[update_end:]
  ast.parse(new2, filename=str(p))
  _backup_write(p, old, new2)
  print(f"PATCHED: {p}")

def main() -> int:
  any_patched = False
  for p in CC_PATHS:
    if p.exists():
      patch_one(p)
      any_patched = True
  if not any_patched:
    raise SystemExit("No carcontroller.py found in expected locations")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())

