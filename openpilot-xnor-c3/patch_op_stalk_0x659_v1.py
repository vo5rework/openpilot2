#!/usr/bin/env python3
"""
Patch XNOR TeslaLegacy to eliminate dual-panda controlsAllowed mismatch by carrying
stalk MAIN/CANCEL edges over Unity-style internal msg 0x659, and sending that msg
to both panda bus blocks (0 and 4).

Edits (with backups):
- opendbc_repo/opendbc/car/tesla/teslacan.py
  * extend create_fake_das_msg() to include stalk_main/stalk_cancel bits (b5 bit1/bit0)
- opendbc_repo/opendbc/car/tesla/carcontroller.py
  * send 0x659 to bus CANBUS.party and CANBUS.party+4
  * include stalk MAIN/CANCEL edge bits
- opendbc_repo/opendbc/safety/modes/tesla_legacy.h
  * in tesla_legacy_tx_hook, parse b5 bits and call pcm_cruise_check(true/false)

Usage:
  sudo python3 /data/openpilot/patch_op_stalk_0x659_v1.py
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path("/data/openpilot")
TESLACAN = ROOT / "opendbc_repo/opendbc/car/tesla/teslacan.py"
CARCONTROLLER = ROOT / "opendbc_repo/opendbc/car/tesla/carcontroller.py"
SAFETY = ROOT / "opendbc_repo/opendbc/safety/modes/tesla_legacy.h"

ILLEGAL_ASSIGN_RE = re.compile(
  r"^\s*(tesla_legacy_op_autopilot_disabled|tesla_legacy_op_pedal_enabled)\s*=\s*false\s*;\s*$",
  re.M,
)

def backup_write(p: Path, new: str) -> None:
  old = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  bak = p.with_suffix(p.suffix + ".bak_0x659_stalk")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")


def patch_teslacan(src: str) -> str:
  # Replace create_fake_das_msg definition block
  pat = re.compile(r"def create_fake_das_msg\([\s\S]*?\n\s*return\s*\(0x659,\s*bytes\(dat\),\s*bus\)\n", re.M)
  repl = (
    "def create_fake_das_msg(self, pedalEnabled: bool, autopilot_disabled: bool, "
    "stalk_main: bool = False, stalk_cancel: bool = False, bus: int = CANBUS.party):\n"
    "    \"\"\"Unity parity: internal openpilot->panda message (0x659) to configure safety.\n"
    "    Byte5 bits:\n"
    "      bit7: autopilot_disabled\n"
    "      bit5: pedalEnabled\n"
    "      bit1: stalk_main (edge)\n"
    "      bit0: stalk_cancel (edge)\n"
    "    Not in any DBC; safety tx_hook consumes and blocks it from hitting the car.\n"
    "    \"\"\"\n"
    "    dat = bytearray(8)\n"
    "    dat[5] = (0x20 if pedalEnabled else 0) | (0x80 if autopilot_disabled else 0) |\n"
    "             (0x02 if stalk_main else 0) | (0x01 if stalk_cancel else 0)\n"
    "    return (0x659, bytes(dat), bus)\n"
  )
  if not pat.search(src):
    raise RuntimeError("teslacan.py: couldn't find create_fake_das_msg() block to replace")
  return pat.sub(repl, src, count=1)


def patch_carcontroller(src: str) -> str:
  # Ensure prev stalk state exists in __init__
  if "_prev_cruise_buttons" not in src:
    init_pat = re.compile(r"(def __init__\([\s\S]*?\n)(\s*self\.frame\s*=\s*0\s*\n)", re.M)
    m = init_pat.search(src)
    if not m:
      raise RuntimeError("carcontroller.py: couldn't find __init__ frame init anchor")
    insert = m.group(1) + m.group(2) + "    self._prev_cruise_buttons = 0\n"
    src = src[:m.start()] + insert + src[m.end():]

  # Replace the fake DAS send block
  block_pat = re.compile(
    r"\n\s*# Send fake DAS msg at 10Hz[\s\S]*?\n\s*if\s*\(self\.frame\s*%\s*10\)\s*==\s*0:\n\s*can_sends\.append\([^\n]*create_fake_das_msg[^\n]*\)\n",
    re.M
  )
  if not block_pat.search(src):
    raise RuntimeError("carcontroller.py: couldn't find the '# Send fake DAS msg' block to replace")

  new_block = (
    "\n    # Send internal DAS msg (0x659) to BOTH panda bus blocks (0 and 4).\n"
    "    # This removes dual-panda controlsAllowed mismatch when only one panda physically sees the stalk.\n"
    "    stalk_btn = int(getattr(CS, 'cruise_buttons', 0))\n"
    "    stalk_main = (stalk_btn == 2)\n"
    "    stalk_cancel = (stalk_btn == 1)\n"
    "    stalk_main_edge = stalk_main and (self._prev_cruise_buttons != 2)\n"
    "    stalk_cancel_edge = stalk_cancel and (self._prev_cruise_buttons != 1)\n"
    "    self._prev_cruise_buttons = stalk_btn\n"
    "\n"
    "    # Always at 10Hz, plus immediately on MAIN/CANCEL edges\n"
    "    if ((self.frame % 10) == 0) or stalk_main_edge or stalk_cancel_edge:\n"
    "      for bus in (CANBUS.party, CANBUS.party + 4):\n"
    "        can_sends.append(self._action_can.create_fake_das_msg(\n"
    "          self._cached_pedal_enabled,\n"
    "          self._cached_autopilot_disabled,\n"
    "          stalk_main=stalk_main_edge,\n"
    "          stalk_cancel=stalk_cancel_edge,\n"
    "          bus=bus,\n"
    "        ))\n"
  )
  src = block_pat.sub(new_block, src, count=1)
  return src


def patch_safety(src: str) -> str:
  src = ILLEGAL_ASSIGN_RE.sub("", src)

  # Replace the 0x659 handler in tesla_legacy_tx_hook
  pat = re.compile(
    r"// UNITY_PARITY_0x659_PEDAL_ENABLED_V19[\s\S]*?if\s*\(msg->addr\s*==\s*0x659U\)\s*\{\s*[\s\S]*?\n\s*return\s+false;\s*\n\s*\}\n",
    re.M,
  )
  if not pat.search(src):
    raise RuntimeError("tesla_legacy.h: couldn't find 0x659 handler block in tx_hook")

  repl = (
    "// UNITY_PARITY_0x659_PEDAL_ENABLED_V19\n"
    "// Fake DAS message used by Unity to pass mode bits to safety; never forward to the car.\n"
    "// Byte5 bits:\n"
    "//   bit7: autopilot_disabled, bit5: pedalEnabled, bit1: stalk_main (edge), bit0: stalk_cancel (edge)\n"
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
  src = pat.sub(repl, src, count=1)

  if ILLEGAL_ASSIGN_RE.search(src):
    raise RuntimeError("tesla_legacy.h: illegal file-scope assignment still present after patch")

  return src


def main() -> int:
  for p in (TESLACAN, CARCONTROLLER, SAFETY):
    if not p.exists():
      raise SystemExit(f"missing file: {p}")

  tc = TESLACAN.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  cc = CARCONTROLLER.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  sf = SAFETY.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

  tc2 = patch_teslacan(tc)
  cc2 = patch_carcontroller(cc)
  sf2 = patch_safety(sf)

  backup_write(TESLACAN, tc2)
  backup_write(CARCONTROLLER, cc2)
  backup_write(SAFETY, sf2)

  print("OK: patched teslacan.py, carcontroller.py, tesla_legacy.h (with .bak_0x659_stalk backups)")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
