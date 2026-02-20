#!/usr/bin/env python3
"""
Dual-panda TeslaLegacy: carry stalk MAIN/CANCEL edges over internal 0x659 and send to both pandas.

This version is tolerant:
- If tesla_checksum is missing in teslacan.py, it appends a canonical implementation.
- It patches BOTH trees if present:
    /data/openpilot/opendbc/...
    /data/openpilot/opendbc_repo/opendbc/...
- No openpilot imports. Syntax-only validation via ast/py_compile.

Edits (with backups):
- teslacan.py: ensure tesla_checksum exists; replace create_fake_das_msg()
- carcontroller.py: send 0x659 on CANBUS.party and CANBUS.party+4; include MAIN/CANCEL edges
- tesla_legacy.h: in tx_hook(0x659) parse edge bits -> pcm_cruise_check(); still return false (never forwarded)
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

ILLEGAL_ASSIGN_RE = re.compile(
  r"^\s*(tesla_legacy_op_autopilot_disabled|tesla_legacy_op_pedal_enabled)\s*=\s*false\s*;\s*$",
  re.M,
)

CREATE_FAKE_DAS_RE = re.compile(
  r"^(\s*)def\s+create_fake_das_msg\s*\([^\n]*\)\s*:\s*\n",
  re.M,
)

FAKE_DAS_BLOCK_RE = re.compile(
  r"\n(?P<ind>\s*)#\s*Send\s+fake\s+DAS\s+msg\s+at\s+10Hz[\s\S]*?\n(?P=ind)if\s*\(\s*self\.frame\s*%\s*10\s*\)\s*==\s*0\s*:\s*\n(?P=ind)\s+can_sends\.append\([^\n]*create_fake_das_msg[^\n]*\)\s*\n",
  re.M
)

TX659_START_RE = re.compile(r"^\s*if\s*\(msg->addr\s*==\s*0x659U\)\s*\{\s*$", re.M)
TX659_END_RE = re.compile(r"^\s*\}\s*$", re.M)


def _norm(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")


def _backup_write(p: Path, old: str, new: str, suffix: str) -> None:
  if new == old:
    return
  bak = p.with_suffix(p.suffix + suffix)
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")


def _replace_def_block(src: str, def_re: re.Pattern, new_block: str) -> str:
  m = def_re.search(src)
  if not m:
    raise RuntimeError("create_fake_das_msg not found")
  indent = m.group(1)
  start = m.start()
  after = src[m.end():]
  n = re.search(rf"^{re.escape(indent)}def\s+\w+\s*\(", after, re.M)
  end = m.end() + (n.start() if n else len(after))
  return src[:start] + new_block + src[end:]


def ensure_tesla_checksum(src: str) -> str:
  if "def tesla_checksum" in src:
    return src
  if not src.endswith("\n"):
    src += "\n"
  return src + "\n" + TESLA_CHECKSUM_BLOCK + "\n"


def patch_teslacan(p: Path) -> bool:
  if not p.exists():
    return False
  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  new = old

  new = ensure_tesla_checksum(new)

  m = CREATE_FAKE_DAS_RE.search(new)
  if not m:
    raise RuntimeError(f"{p}: create_fake_das_msg not found")
  ind = m.group(1)

  block = (
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

  new = _replace_def_block(new, CREATE_FAKE_DAS_RE, block)

  # Syntax validate without importing openpilot
  ast.parse(new, filename=str(p))
  _backup_write(p, old, new, ".bak_dualpanda659_v2")
  py_compile.compile(str(p), doraise=True)
  return True


def patch_carcontroller(p: Path) -> bool:
  if not p.exists():
    return False
  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  new = old

  if "_prev_cruise_buttons" not in new:
    anchor = re.search(r"(\n\s*self\.frame\s*=\s*0\s*\n)", new)
    if anchor:
      i = anchor.end(1)
      new = new[:i] + "    self._prev_cruise_buttons = 0\n" + new[i:]

  m = FAKE_DAS_BLOCK_RE.search(new)
  if not m:
    raise RuntimeError(f"{p}: couldn't find '# Send fake DAS msg at 10Hz' block")

  ind = m.group("ind")
  repl = (
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
  new = new[:m.start()] + repl + new[m.end():]

  ast.parse(new, filename=str(p))
  _backup_write(p, old, new, ".bak_dualpanda659_v2")
  py_compile.compile(str(p), doraise=True)
  return True


def patch_safety(p: Path) -> bool:
  if not p.exists():
    return False
  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  new = ILLEGAL_ASSIGN_RE.sub("", old)

  # Find the "if (msg->addr == 0x659U) {" block and replace its contents.
  start_m = TX659_START_RE.search(new)
  if not start_m:
    raise RuntimeError(f"{p}: couldn't find tx_hook 0x659 block start")

  # Find matching end brace line after start
  lines = new.splitlines(True)
  start_idx = None
  for i, line in enumerate(lines):
    if start_idx is None and TX659_START_RE.search(line):
      start_idx = i
      continue
    if start_idx is not None and TX659_END_RE.match(line.strip()):
      end_idx = i
      break
  else:
    raise RuntimeError(f"{p}: couldn't find end of 0x659 block")

  indent = re.match(r"^(\s*)", lines[start_idx]).group(1)
  inner = indent + "  "

  replaced = (
    lines[:start_idx+1] +
    [f"{inner}const uint8_t b5 = (uint8_t)GET_BYTES(msg, 5U, 1U);\n",
     f"{inner}tesla_legacy_op_autopilot_disabled = (b5 & 0x80U) != 0U;  // bit7\n",
     f"{inner}tesla_legacy_op_pedal_enabled = (b5 & 0x20U) != 0U;       // bit5\n",
     f"{inner}const bool stalk_main = (b5 & 0x02U) != 0U;               // bit1\n",
     f"{inner}const bool stalk_cancel = (b5 & 0x01U) != 0U;             // bit0\n",
     f"{inner}if (stalk_main) {{\n",
     f"{inner}  pcm_cruise_check(true);\n",
     f"{inner}}} else if (stalk_cancel) {{\n",
     f"{inner}  pcm_cruise_check(false);\n",
     f"{inner}}}\n",
     f"{inner}return false;\n"] +
    [lines[end_idx]] +
    lines[end_idx+1:]
  )

  new2 = "".join(replaced)
  if ILLEGAL_ASSIGN_RE.search(new2):
    raise RuntimeError(f"{p}: illegal file-scope assignment still present")
  _backup_write(p, old, new2, ".bak_dualpanda659_v2")
  return True


def main() -> int:
  for p in TESLACAN_PATHS:
    patch_teslacan(p)
  for p in CARCONTROLLER_PATHS:
    patch_carcontroller(p)
  for p in SAFETY_PATHS:
    patch_safety(p)
  print("OK: applied dual-panda 0x659 carrier patches (v2). Backups: *.bak_dualpanda659_v2")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
