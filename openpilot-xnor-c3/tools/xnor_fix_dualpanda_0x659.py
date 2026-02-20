#!/usr/bin/env python3
"""
Fix dual-panda controlsAllowed mismatch for TeslaLegacy by carrying stalk edges via 0x659
and sending 0x659 to both panda bus blocks (0 and 4).

Edits (with backups):
- teslacan.py: replace create_fake_das_msg() (keep tesla_checksum)
- carcontroller.py: send 0x659 to CANBUS.party and CANBUS.party+4; add edge bits
- tesla_legacy.h: in tx_hook(0x659) parse edge bits + pcm_cruise_check(); still return false

No imports from openpilot/opendbc at runtime. Uses py_compile for syntax checks only.
"""

from __future__ import annotations

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

ILLEGAL_ASSIGN_RE = re.compile(
  r"^\s*(tesla_legacy_op_autopilot_disabled|tesla_legacy_op_pedal_enabled)\s*=\s*false\s*;\s*$",
  re.M,
)

CREATE_FAKE_DAS_RE = re.compile(
  r"^(\s*)def\s+create_fake_das_msg\s*\([^\n]*\)\s*:\s*\n",
  re.M,
)

FAKE_DAS_BLOCK_RE = re.compile(
  r"\n(\s*)#\s*Send\s+fake\s+DAS\s+msg\s+at\s+10Hz[\s\S]*?\n\1if\s*\(\s*self\.frame\s*%\s*10\s*\)\s*==\s*0\s*:\s*\n\1\s+can_sends\.append\([^\n]*create_fake_das_msg[^\n]*\)\s*\n",
  re.M,
)

TX659_RE = re.compile(
  r"(^\s*//\s*UNITY_PARITY_0x659_PEDAL_ENABLED_V19[\s\S]*?^\s*if\s*\(msg->addr\s*==\s*0x659U\)\s*\{\s*\n)"
  r"([\s\S]*?)"
  r"(^\s*\}\s*\n)",
  re.M,
)


def _backup_write(p: Path, new: str, suffix: str) -> None:
  old = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  if new == old:
    return
  bak = p.with_suffix(p.suffix + suffix)
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")


def _replace_def_block(src: str, def_re: re.Pattern, new_block: str) -> str:
  m = def_re.search(src)
  if not m:
    return src

  indent = m.group(1)
  start = m.start()

  after = src[m.end():]
  n = re.search(rf"^{re.escape(indent)}def\s+\w+\s*\(", after, re.M)
  end = m.end() + (n.start() if n else len(after))

  return src[:start] + new_block + src[end:]


def patch_teslacan(p: Path) -> bool:
  if not p.exists():
    return False
  s = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

  if "def tesla_checksum" not in s:
    raise RuntimeError(f"{p}: tesla_checksum missing (won't touch until restored)")

  m = CREATE_FAKE_DAS_RE.search(s)
  if not m:
    raise RuntimeError(f"{p}: create_fake_das_msg not found")

  ind = m.group(1)
  new_block = (
    f"{ind}def create_fake_das_msg(self, pedalEnabled: bool, autopilot_disabled: bool, bus: int = CANBUS.party, *,\n"
    f"{ind}                        stalk_main: bool = False, stalk_cancel: bool = False):\n"
    f'{ind}  """Internal openpilot->panda msg (0x659). Safety consumes + blocks it from the car.\n'
    f"{ind}  Byte5 bits: bit7=autopilot_disabled, bit5=pedalEnabled, bit1=stalk_main(edge), bit0=stalk_cancel(edge)\n"
    f'{ind}  """\n'
    f"{ind}  dat = bytearray(8)\n"
    f"{ind}  dat[5] = ((0x20 if pedalEnabled else 0) |\n"
    f"{ind}            (0x80 if autopilot_disabled else 0) |\n"
    f"{ind}            (0x02 if stalk_main else 0) |\n"
    f"{ind}            (0x01 if stalk_cancel else 0))\n"
    f"{ind}  return (0x659, bytes(dat), bus)\n\n"
  )

  s2 = _replace_def_block(s, CREATE_FAKE_DAS_RE, new_block)
  _backup_write(p, s2, ".bak_dualpanda659")
  py_compile.compile(str(p), doraise=True)
  return True


def patch_carcontroller(p: Path) -> bool:
  if not p.exists():
    return False
  s = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

  # Ensure prev state member exists
  if "_prev_cruise_buttons" not in s:
    init_anchor = re.search(r"(\n\s*self\.frame\s*=\s*0\s*\n)", s)
    if init_anchor:
      ins_at = init_anchor.end(1)
      s = s[:ins_at] + "    self._prev_cruise_buttons = 0\n" + s[ins_at:]

  # Replace fake DAS block
  m = FAKE_DAS_BLOCK_RE.search(s)
  if not m:
    raise RuntimeError(f"{p}: couldn't find 'Send fake DAS msg at 10Hz' block")

  ind = m.group(1)
  new_block = (
    f"\n{ind}# Send internal DAS msg (0x659) to BOTH panda bus blocks (0 and 4).\n"
    f"{ind}# Carries MAIN/CANCEL edges so both pandas latch controlsAllowed even if only one sees STW_ACTN_RQ.\n"
    f"{ind}stalk_btn = int(getattr(CS, 'cruise_buttons', 0))\n"
    f"{ind}stalk_main = (stalk_btn == 2)\n"
    f"{ind}stalk_cancel = (stalk_btn == 1)\n"
    f"{ind}stalk_main_edge = stalk_main and (self._prev_cruise_buttons != 2)\n"
    f"{ind}stalk_cancel_edge = stalk_cancel and (self._prev_cruise_buttons != 1)\n"
    f"{ind}self._prev_cruise_buttons = stalk_btn\n"
    f"\n{ind}if ((self.frame % 10) == 0) or stalk_main_edge or stalk_cancel_edge:\n"
    f"{ind}  for bus in (CANBUS.party, CANBUS.party + 4):\n"
    f"{ind}    can_sends.append(self._action_can.create_fake_das_msg(\n"
    f"{ind}      self._cached_pedal_enabled,\n"
    f"{ind}      self._cached_autopilot_disabled,\n"
    f"{ind}      bus,\n"
    f"{ind}      stalk_main=stalk_main_edge,\n"
    f"{ind}      stalk_cancel=stalk_cancel_edge,\n"
    f"{ind}    ))\n"
  )

  s2 = s[:m.start()] + new_block + s[m.end():]
  _backup_write(p, s2, ".bak_dualpanda659")
  py_compile.compile(str(p), doraise=True)
  return True


def patch_safety(p: Path) -> bool:
  if not p.exists():
    return False
  s = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

  s = ILLEGAL_ASSIGN_RE.sub("", s)

  m = TX659_RE.search(s)
  if not m:
    raise RuntimeError(f"{p}: couldn't find tx_hook 0x659 block")

  head, body, tail = m.group(1), m.group(2), m.group(3)

  # Replace body with edge-bit handling
  new_body = (
    "    const uint8_t b5 = (uint8_t)GET_BYTES(msg, 5U, 1U);\n"
    "    tesla_legacy_op_autopilot_disabled = (b5 & 0x80U) != 0U;  // bit7\n"
    "    tesla_legacy_op_pedal_enabled = (b5 & 0x20U) != 0U;       // bit5\n"
    "    const bool stalk_main = (b5 & 0x02U) != 0U;               // bit1\n"
    "    const bool stalk_cancel = (b5 & 0x01U) != 0U;             // bit0\n"
    "    if (stalk_main) {\n"
    "      pcm_cruise_check(true);\n"
    "    } else if (stalk_cancel) {\n"
    "      pcm_cruise_check(false);\n"
    "    }\n"
    "    return false;\n"
  )

  s2 = s[:m.start()] + head + new_body + tail + s[m.end():]
  if ILLEGAL_ASSIGN_RE.search(s2):
    raise RuntimeError(f"{p}: illegal file-scope assignment still present after patch")
  _backup_write(p, s2, ".bak_dualpanda659")
  return True


def main() -> int:
  changed = False

  for p in TESLACAN_PATHS:
    changed |= patch_teslacan(p)

  for p in CARCONTROLLER_PATHS:
    changed |= patch_carcontroller(p)

  for p in SAFETY_PATHS:
    changed |= patch_safety(p)

  print("OK: patched dual-panda 0x659 carrier. Backups saved as *.bak_dualpanda659")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
