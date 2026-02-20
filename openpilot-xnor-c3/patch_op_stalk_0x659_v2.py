#!/usr/bin/env python3
"""
Robust patch: carry stalk MAIN/CANCEL edges over internal 0x659 to eliminate dual-panda controlsAllowed mismatch.

Why:
- Your stalk (0x45) appears on bus 0 and "returned" bus 130, but NOT on bus 4.
- Panda with safetyParam=42 is on bus block 4; it never sees 0x45, so it can never latch.
- Unity solved this with a "gateway"; in opendbc filter-land we emulate via internal carrier 0x659.

Edits (with .bak_0x659_v2 backups):
- opendbc_repo/opendbc/car/tesla/teslacan.py
  - extend create_fake_das_msg() to accept stalk_main/stalk_cancel and encode into byte5 bits 1/0
- opendbc_repo/opendbc/car/tesla/carcontroller.py
  - replace the existing 10Hz 0x659 send block with a block that:
      - detects MAIN/CANCEL edges from CS.cruise_buttons
      - sends 0x659 to BOTH CANBUS.party and CANBUS.party+4
      - sends at 10Hz and immediately on edges
- opendbc_repo/opendbc/safety/modes/tesla_legacy.h
  - in tesla_legacy_tx_hook, parse byte5 bits and call pcm_cruise_check(true/false) on stalk edges
  - return false to block 0x659 from reaching the car

Usage:
  sudo python3 /data/openpilot/patch_op_stalk_0x659_v2.py
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path("/data/openpilot")
TESLACAN = ROOT / "opendbc_repo/opendbc/car/tesla/teslacan.py"
CARCONTROLLER = ROOT / "opendbc_repo/opendbc/car/tesla/carcontroller.py"
SAFETY = ROOT / "opendbc_repo/opendbc/safety/modes/tesla_legacy.h"

MARK_CC = "# OP_STALK_0x659_EDGE_CARRIER_V2"
MARK_SF = "// OP_STALK_0x659_EDGE_CARRIER_V2"

ILLEGAL_ASSIGN_RE = re.compile(
  r"^\s*(tesla_legacy_op_autopilot_disabled|tesla_legacy_op_pedal_enabled)\s*=\s*false\s*;\s*$",
  re.M,
)

def read_norm(p: Path) -> str:
  return p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

def backup_write(p: Path, old: str, new: str) -> None:
  bak = p.with_suffix(p.suffix + ".bak_0x659_v2")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")

def patch_teslacan(src: str) -> str:
  if "stalk_main" in src and "stalk_cancel" in src and "0x659" in src:
    return src  # already extended

  # Find the existing create_fake_das_msg definition (any signature), replace whole function body.
  m = re.search(r"^def\s+create_fake_das_msg\s*\(.*?\):\s*\n(?:^[ \t]+.*\n)+", src, re.M)
  if not m:
    raise RuntimeError("teslacan.py: couldn't find def create_fake_das_msg(...)")

  fn = (
    "def create_fake_das_msg(self, pedalEnabled: bool, autopilot_disabled: bool, bus: int, "
    "stalk_main: bool = False, stalk_cancel: bool = False):\n"
    "    \"\"\"Internal openpilot->panda message (0x659), consumed by tesla_legacy safety.\n"
    "    Byte5 bits:\n"
    "      bit7: autopilot_disabled\n"
    "      bit5: pedalEnabled\n"
    "      bit1: stalk_main (edge)\n"
    "      bit0: stalk_cancel (edge)\n"
    "    Safety tx_hook consumes + blocks it; it never hits the car.\n"
    "    \"\"\"\n"
    "    dat = bytearray(8)\n"
    "    dat[5] = (0x20 if pedalEnabled else 0) | (0x80 if autopilot_disabled else 0) | \\\n"
    "             (0x02 if stalk_main else 0) | (0x01 if stalk_cancel else 0)\n"
    "    return (0x659, bytes(dat), bus)\n"
  )

  return src[:m.start()] + fn + src[m.end():]

def patch_carcontroller(src: str) -> str:
  if MARK_CC in src:
    return src

  # Replace the existing "10Hz" block that appends create_fake_das_msg.
  # This matches: if ((self.frame % 10) == 0): followed by one or more can_sends.append(...create_fake_das_msg...)
  block_re = re.compile(
    r"(?P<indent>^[ \t]*)if\s*\(\s*\(?\s*self\.frame\s*%\s*10\s*\)?\s*\)\s*==\s*0\s*:\s*\n"
    r"(?:(?P=indent)[ \t]+can_sends\.append\([^\n]*create_fake_das_msg[^\n]*\)\s*\n)+",
    re.M,
  )
  m = block_re.search(src)
  if not m:
    raise RuntimeError("carcontroller.py: couldn't find the existing 10Hz create_fake_das_msg send block")

  ind = m.group("indent")

  # Use getattr(self, ...) so we don't need to touch __init__.
  new_block = (
    f"{ind}{MARK_CC}\n"
    f"{ind}# Send internal DAS msg (0x659) to BOTH panda bus blocks (party and party+4).\n"
    f"{ind}# This eliminates controlsAllowed mismatch when only one panda sees the real stalk.\n"
    f"{ind}stalk_btn = int(getattr(CS, 'cruise_buttons', 0))\n"
    f"{ind}prev_btn = int(getattr(self, '_prev_cruise_buttons', 0))\n"
    f"{ind}stalk_main_edge = (stalk_btn == 2) and (prev_btn != 2)\n"
    f"{ind}stalk_cancel_edge = (stalk_btn == 1) and (prev_btn != 1)\n"
    f"{ind}self._prev_cruise_buttons = stalk_btn\n"
    f"\n"
    f"{ind}if ((self.frame % 10) == 0) or stalk_main_edge or stalk_cancel_edge:\n"
    f"{ind}  for bus in (CANBUS.party, CANBUS.party + 4):\n"
    f"{ind}    can_sends.append(self._action_can.create_fake_das_msg(\n"
    f"{ind}      self._cached_pedal_enabled,\n"
    f"{ind}      self._cached_autopilot_disabled,\n"
    f"{ind}      bus,\n"
    f"{ind}      stalk_main=stalk_main_edge,\n"
    f"{ind}      stalk_cancel=stalk_cancel_edge,\n"
    f"{ind}    ))\n"
  )

  return src[:m.start()] + new_block + src[m.end():]

def patch_safety(src: str) -> str:
  if MARK_SF in src:
    return src

  src = ILLEGAL_ASSIGN_RE.sub("", src)

  # Replace the whole `if (msg->addr == 0x659U) { ... }` block inside tx_hook.
  # This is intentionally broad and only targets the first such block.
  blk_re = re.compile(
    r"^[ \t]*if\s*\(\s*msg->addr\s*==\s*0x659U\s*\)\s*\{\s*\n"
    r"(?:^[ \t]+.*\n)*?"
    r"^[ \t]*\}\s*\n",
    re.M,
  )
  m = blk_re.search(src)
  if not m:
    raise RuntimeError("tesla_legacy.h: couldn't find `if (msg->addr == 0x659U) { ... }` block")

  repl = (
    f"  {MARK_SF}\n"
    "  // Internal carrier (0x659): consumed by safety, never forwarded to the car.\n"
    "  // Byte5 bits:\n"
    "  //   bit7: autopilot_disabled, bit5: pedalEnabled, bit1: stalk_main(edge), bit0: stalk_cancel(edge)\n"
    "  if (msg->addr == 0x659U) {\n"
    "    const uint8_t b5 = (uint8_t)GET_BYTES(msg, 5U, 1U);\n"
    "    tesla_legacy_op_autopilot_disabled = (b5 & 0x80U) != 0U;\n"
    "    tesla_legacy_op_pedal_enabled = (b5 & 0x20U) != 0U;\n"
    "    const bool stalk_main = (b5 & 0x02U) != 0U;\n"
    "    const bool stalk_cancel = (b5 & 0x01U) != 0U;\n"
    "    if (stalk_main) {\n"
    "      pcm_cruise_check(true);\n"
    "    } else if (stalk_cancel) {\n"
    "      pcm_cruise_check(false);\n"
    "    }\n"
    "    return false;\n"
    "  }\n"
  )

  out = src[:m.start()] + repl + src[m.end():]

  if ILLEGAL_ASSIGN_RE.search(out):
    raise RuntimeError("tesla_legacy.h: illegal file-scope assignment still present after patch")

  return out

def main() -> int:
  for p in (TESLACAN, CARCONTROLLER, SAFETY):
    if not p.exists():
      raise SystemExit(f"missing file: {p}")

  tc0 = read_norm(TESLACAN)
  cc0 = read_norm(CARCONTROLLER)
  sf0 = read_norm(SAFETY)

  tc1 = patch_teslacan(tc0)
  cc1 = patch_carcontroller(cc0)
  sf1 = patch_safety(sf0)

  backup_write(TESLACAN, tc0, tc1)
  backup_write(CARCONTROLLER, cc0, cc1)
  backup_write(SAFETY, sf0, sf1)

  print("OK: patched teslacan.py, carcontroller.py, tesla_legacy.h (backups: *.bak_0x659_v2)")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
