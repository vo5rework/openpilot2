#!/usr/bin/env python3
"""
Dual-panda TeslaLegacy fix (v3.1):

Goal:
- Send internal carrier 0x659 to BOTH pandas (bus 0 and bus 4)
- Carry MAIN/CANCEL edges in byte5 so both pandas latch controlsAllowed
- Safety consumes 0x659 and calls pcm_cruise_check(true/false); never forwards to car

Robustness:
- Does NOT assume create_fake_das_msg exists; adds module-level helper if missing
- Injects before any 'return ... can_sends ...' (tuple or not)
- Patches both trees when present:
    /data/openpilot/opendbc/...
    /data/openpilot/opendbc_repo/opendbc/...
- No openpilot imports at patch-time; syntax checks only

Backups:
- *.bak_dualpanda659_v3_1
"""

from __future__ import annotations

import ast
import re
import py_compile
from pathlib import Path


ROOT = Path("/data/openpilot")

TESLACAN_PATHS = [
  ROOT / "opendbc/car/tesla/teslacan.py",
  ROOT / "opendbc_repo/opendbc/car/tesla/teslacan.py",
]
CARCONTROLLER_PATHS = [
  ROOT / "opendbc/car/tesla/carcontroller.py",
  ROOT / "opendbc_repo/opendbc/car/tesla/carcontroller.py",
]
SAFETY_PATHS = [
  ROOT / "opendbc_repo/opendbc/safety/modes/tesla_legacy.h",
  ROOT / "opendbc/safety/modes/tesla_legacy.h",
]

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

DEF_UPDATE_RE = re.compile(r"^(\s*)def\s+update\s*\(.*\)\s*:\s*$", re.M)
RETURN_CANSENDS_RE = re.compile(r"^(?P<ind>\s+)return\b[^\n]*\bcan_sends\b[^\n]*$", re.M)


def _norm(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")


def _backup_write(p: Path, old: str, new: str) -> None:
  if new == old:
    return
  bak = p.with_suffix(p.suffix + ".bak_dualpanda659_v3_1")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")


def _ensure_top_level_symbol(src: str, needle: str, block: str) -> str:
  if needle in src:
    return src
  if not src.endswith("\n"):
    src += "\n"
  return src + "\n" + block + "\n"


def patch_teslacan(p: Path) -> bool:
  if not p.exists():
    return False

  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  new = old
  new = _ensure_top_level_symbol(new, "def tesla_checksum", TESLA_CHECKSUM_BLOCK)
  new = _ensure_top_level_symbol(new, "def create_fake_das_msg", CREATE_FAKE_DAS_HELPER)

  ast.parse(new, filename=str(p))
  _backup_write(p, old, new)
  py_compile.compile(str(p), doraise=True)
  return True


def _insert_import_if_missing(src: str, import_line: str) -> str:
  if import_line in src:
    return src
  lines = src.splitlines(True)
  ins = 0
  for i, line in enumerate(lines):
    if line.startswith("import ") or line.startswith("from "):
      ins = i + 1
    elif i > 20:
      break
  lines.insert(ins, import_line + "\n")
  return "".join(lines)


def patch_carcontroller(p: Path) -> bool:
  if not p.exists():
    return False

  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  new = old

  new = _insert_import_if_missing(new, "from opendbc.car.tesla.teslacan import create_fake_das_msg")

  m_up = DEF_UPDATE_RE.search(new)
  if not m_up:
    raise RuntimeError(f"{p}: couldn't find def update(...)")

  update_indent = m_up.group(1)
  after = new[m_up.end():]
  m_next = re.search(rf"^{re.escape(update_indent)}def\s+\w+\s*\(", after, re.M)
  update_end = m_up.end() + (m_next.start() if m_next else len(after))
  update_block = new[m_up.start():update_end]

  returns = list(RETURN_CANSENDS_RE.finditer(update_block))
  if not returns:
    ret_lines = [ln for ln in update_block.splitlines() if ln.strip().startswith("return")][:20]
    raise RuntimeError("carcontroller.py: couldn't find a return containing can_sends. Returns seen:\n" + "\n".join(ret_lines))

  # Insert before the LAST return that includes can_sends
  m_ret = returns[-1]
  ind = m_ret.group("ind")
  insert_at = m_ret.start()

  inject = (
    f"\n{ind}# Dual-panda carrier: send internal 0x659 to bus 0 and bus 4.\n"
    f"{ind}# Prevents controlsAllowed mismatch when only one panda sees STW_ACTN_RQ (0x45).\n"
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

  update_block2 = update_block[:insert_at] + inject + update_block[insert_at:]
  new2 = new[:m_up.start()] + update_block2 + new[update_end:]

  ast.parse(new2, filename=str(p))
  _backup_write(p, old, new2)
  py_compile.compile(str(p), doraise=True)
  return True


def patch_safety(p: Path) -> bool:
  if not p.exists():
    return False

  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  new = ILLEGAL_ASSIGN_RE.sub("", old)

  # Find "if (msg->addr == 0x659U) {" and replace body using brace counting
  lines = new.splitlines(True)
  start = None
  for i, ln in enumerate(lines):
    if re.search(r"\bif\s*\(msg->addr\s*==\s*0x659U\)\s*\{", ln):
      start = i
      break
  if start is None:
    raise RuntimeError(f"{p}: couldn't find tx_hook 0x659 block")

  brace = 0
  end = None
  for j in range(start, len(lines)):
    brace += lines[j].count("{")
    brace -= lines[j].count("}")
    if j > start and brace == 0:
      end = j
      break
  if end is None:
    raise RuntimeError(f"{p}: couldn't find end of 0x659 block")

  base_indent = re.match(r"^(\s*)", lines[start]).group(1)
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

  # Keep the opening line, replace interior, keep closing brace line
  new2 = "".join(lines[:start+1] + body + [lines[end]] + lines[end+1:])
  if ILLEGAL_ASSIGN_RE.search(new2):
    raise RuntimeError(f"{p}: illegal file-scope assignment still present")
  _backup_write(p, old, new2)
  return True


def main() -> int:
  any_tc = any(patch_teslacan(p) for p in TESLACAN_PATHS)
  any_cc = any(patch_carcontroller(p) for p in CARCONTROLLER_PATHS)
  any_sf = any(patch_safety(p) for p in SAFETY_PATHS)

  if not any_tc:
    raise SystemExit("No teslacan.py patched (paths missing)")
  if not any_cc:
    raise SystemExit("No carcontroller.py patched (paths missing)")
  if not any_sf:
    raise SystemExit("No tesla_legacy.h patched (paths missing)")

  print("OK: dual-panda 0x659 carrier patched (v3.1). Backups: *.bak_dualpanda659_v3_1")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
