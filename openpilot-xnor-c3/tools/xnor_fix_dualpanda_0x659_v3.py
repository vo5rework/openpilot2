#!/usr/bin/env python3
"""
Dual-panda TeslaLegacy fix (robust):
- Ensure opendbc/car/tesla/teslacan.py exports create_fake_das_msg() at module scope
- Patch opendbc/car/tesla/carcontroller.py to send 0x659 to bus 0 and bus 4 (10Hz + MAIN/CANCEL edges)
- Patch opendbc_repo/opendbc/safety/modes/tesla_legacy.h tx_hook to consume 0x659 edge bits -> pcm_cruise_check()

No openpilot/opendbc imports at runtime. Syntax checks only.
Backups: *.bak_dualpanda659_v3
"""

from __future__ import annotations

import ast
import re
import py_compile
from pathlib import Path


ROOT = Path("/data/openpilot")

TESLACAN = ROOT / "opendbc/car/tesla/teslacan.py"
CARCONTROLLER = ROOT / "opendbc/car/tesla/carcontroller.py"
SAFETY = ROOT / "opendbc_repo/opendbc/safety/modes/tesla_legacy.h"

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

CREATE_FAKE_DAS_HELPER = """
def create_fake_das_msg(pedal_enabled: bool, autopilot_disabled: bool, bus: int,
                        stalk_main: bool = False, stalk_cancel: bool = False):
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
"""

ILLEGAL_ASSIGN_RE = re.compile(
  r"^\s*(tesla_legacy_op_autopilot_disabled|tesla_legacy_op_pedal_enabled)\s*=\s*false\s*;\s*$",
  re.M,
)

def _norm(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")

def _backup_write(p: Path, old: str, new: str) -> None:
  if new == old:
    return
  bak = p.with_suffix(p.suffix + ".bak_dualpanda659_v3")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")

def patch_teslacan() -> None:
  if not TESLACAN.exists():
    raise SystemExit(f"missing: {TESLACAN}")
  old = _norm(TESLACAN.read_text(encoding="utf-8", errors="replace"))
  new = old

  if "def tesla_checksum" not in new:
    if not new.endswith("\n"):
      new += "\n"
    new += "\n" + TESLA_CHECKSUM_BLOCK + "\n"

  if "def create_fake_das_msg" not in new:
    if not new.endswith("\n"):
      new += "\n"
    new += "\n" + CREATE_FAKE_DAS_HELPER + "\n"

  ast.parse(new, filename=str(TESLACAN))
  _backup_write(TESLACAN, old, new)
  py_compile.compile(str(TESLACAN), doraise=True)

def patch_carcontroller() -> None:
  if not CARCONTROLLER.exists():
    raise SystemExit(f"missing: {CARCONTROLLER}")
  old = _norm(CARCONTROLLER.read_text(encoding="utf-8", errors="replace"))
  new = old

  # Ensure import exists
  if "create_fake_das_msg" not in new:
    # insert after existing imports (best-effort)
    lines = new.splitlines(True)
    ins = 0
    for i, line in enumerate(lines):
      if line.startswith("import ") or line.startswith("from "):
        ins = i + 1
      else:
        if i > 5:
          break
    lines.insert(ins, "from opendbc.car.tesla.teslacan import create_fake_das_msg\n")
    new = "".join(lines)

  # Inject send block before 'return can_sends' inside update()
  m_update = re.search(r"^(\s*)def\s+update\s*\(.*\)\s*:\s*$", new, re.M)
  if not m_update:
    raise RuntimeError("carcontroller.py: couldn't find def update(...)")
  update_indent = m_update.group(1)

  # find end of update by next def at same indent
  after = new[m_update.end():]
  m_next = re.search(rf"^{re.escape(update_indent)}def\s+\w+\s*\(", after, re.M)
  update_end = m_update.end() + (m_next.start() if m_next else len(after))
  update_block = new[m_update.start():update_end]

  # find a return that returns can_sends
  m_ret = re.search(rf"^(?P<ind>{re.escape(update_indent)}\s+)return\s+can_sends\b.*$", update_block, re.M)
  if not m_ret:
    # print hints (top 10 return lines)
    returns = [ln for ln in update_block.splitlines() if "return" in ln][:10]
    raise RuntimeError("carcontroller.py: couldn't find 'return can_sends'. Returns seen:\n" + "\n".join(returns))

  ind = m_ret.group("ind")
  inject = (
    f"\n{ind}# Dual-panda carrier: send internal 0x659 to bus 0 and bus 4.\n"
    f"{ind}# This prevents controlsAllowed mismatch when only one panda sees STW_ACTN_RQ (0x45).\n"
    f"{ind}stalk_btn = int(getattr(CS, 'cruise_buttons', 0))\n"
    f"{ind}prev_btn = int(getattr(self, '_prev_cruise_buttons', 0))\n"
    f"{ind}stalk_main_edge = (stalk_btn == 2) and (prev_btn != 2)\n"
    f"{ind}stalk_cancel_edge = (stalk_btn == 1) and (prev_btn != 1)\n"
    f"{ind}self._prev_cruise_buttons = stalk_btn\n"
    f"{ind}try:\n"
    f"{ind}  from openpilot.common.params import Params\n"
    f"{ind}  ap_disabled = Params().get_bool('TinklaAutopilotDisabled')\n"
    f"{ind}  pedal_en = Params().get_bool('TinklaPedalEnabled')\n"
    f"{ind}except Exception:\n"
    f"{ind}  ap_disabled = True\n"
    f"{ind}  pedal_en = False\n"
    f"{ind}if ((self.frame % 10) == 0) or stalk_main_edge or stalk_cancel_edge:\n"
    f"{ind}  for bus in (0, 4):\n"
    f"{ind}    can_sends.append(create_fake_das_msg(pedal_en, ap_disabled, bus,\n"
    f"{ind}                                   stalk_main=stalk_main_edge,\n"
    f"{ind}                                   stalk_cancel=stalk_cancel_edge))\n"
  )

  # insert inject immediately before the matched return line
  ret_line_start = m_ret.start()
  update_block2 = update_block[:ret_line_start] + inject + update_block[ret_line_start:]
  new2 = new[:m_update.start()] + update_block2 + new[update_end:]

  ast.parse(new2, filename=str(CARCONTROLLER))
  _backup_write(CARCONTROLLER, old, new2)
  py_compile.compile(str(CARCONTROLLER), doraise=True)

def patch_safety() -> None:
  if not SAFETY.exists():
    raise SystemExit(f"missing: {SAFETY}")
  old = _norm(SAFETY.read_text(encoding="utf-8", errors="replace"))
  new = ILLEGAL_ASSIGN_RE.sub("", old)

  # Replace the contents of: if (msg->addr == 0x659U) { ... }
  m = re.search(r"^\s*if\s*\(msg->addr\s*==\s*0x659U\)\s*\{\s*$", new, re.M)
  if not m:
    raise RuntimeError("tesla_legacy.h: couldn't find 'if (msg->addr == 0x659U)'")

  lines = new.splitlines(True)
  start_idx = None
  for i, ln in enumerate(lines):
    if start_idx is None and re.search(r"if\s*\(msg->addr\s*==\s*0x659U\)", ln):
      start_idx = i
      continue
    if start_idx is not None and re.match(r"^\s*\}\s*$", ln.strip()):
      end_idx = i
      break
  else:
    raise RuntimeError("tesla_legacy.h: couldn't find end brace for 0x659 block")

  base_indent = re.match(r"^(\s*)", lines[start_idx]).group(1)
  inner = base_indent + "  "

  body = [
    f"{inner}const uint8_t b5 = (uint8_t)GET_BYTES(msg, 5U, 1U);\n",
    f"{inner}tesla_legacy_op_autopilot_disabled = (b5 & 0x80U) != 0U;  // bit7\n",
    f"{inner}tesla_legacy_op_pedal_enabled = (b5 & 0x20U) != 0U;       // bit5\n",
    f"{inner}const bool stalk_main = (b5 & 0x02U) != 0U;               // bit1\n",
    f"{inner}const bool stalk_cancel = (b5 & 0x01U) != 0U;             // bit0\n",
    f"{inner}if (stalk_main) {{\n",
    f"{inner}  pcm_cruise_check(true);\n",
    f"{inner}}} else if (stalk_cancel) {{\n",
    f"{inner}  pcm_cruise_check(false);\n",
    f"{inner}}}\n",
    f"{inner}return false;\n",
  ]

  new2 = "".join(lines[:start_idx+1] + body + [lines[end_idx]] + lines[end_idx+1:])
  if ILLEGAL_ASSIGN_RE.search(new2):
    raise RuntimeError("tesla_legacy.h: illegal file-scope assignment still present")
  _backup_write(SAFETY, old, new2)

def main() -> int:
  patch_teslacan()
  patch_carcontroller()
  patch_safety()
  print("OK: dual-panda 0x659 carrier patched (v3). Backups: *.bak_dualpanda659_v3")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
