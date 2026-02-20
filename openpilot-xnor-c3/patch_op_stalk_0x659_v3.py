#!/usr/bin/env python3
"""
OP_STALK 0x659 edge-carrier patch (v3, discovery-based).

Fixes dual-panda controlsAllowed mismatch when only one panda sees real stalk (0x45).

Strategy:
- Carry MAIN/CANCEL edges over internal carrier msg 0x659 (byte5 bits 1/0).
- Send 0x659 to both panda bus blocks (CANBUS.party and CANBUS.party+4).
- In panda safety tx_hook, parse bits and call pcm_cruise_check(true/false); block 0x659 from forwarding.

This script:
- Finds and patches the actual Python file that defines create_fake_das_msg (or create_fake_das_message).
- Patches carcontroller.py send block (idempotent with marker).
- Patches tesla_legacy.h 0x659 handler (idempotent with marker).
- Creates .bak_0x659_v3 backups for any modified file.

Usage:
  sudo python3 /data/openpilot/patch_op_stalk_0x659_v3.py
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path("/data/openpilot")
TESLA_DIR = ROOT / "opendbc_repo/opendbc/car/tesla"
CARCONTROLLER = TESLA_DIR / "carcontroller.py"
SAFETY = ROOT / "opendbc_repo/opendbc/safety/modes/tesla_legacy.h"

MARK_CC = "# OP_STALK_0x659_EDGE_CARRIER_V3"
MARK_SF = "// OP_STALK_0x659_EDGE_CARRIER_V3"

ILLEGAL_ASSIGN_RE = re.compile(
  r"^\s*(tesla_legacy_op_autopilot_disabled|tesla_legacy_op_pedal_enabled)\s*=\s*false\s*;\s*$",
  re.M,
)

def read_norm(p: Path) -> str:
  return p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

def backup_write(p: Path, old: str, new: str) -> None:
  if old == new:
    return
  bak = p.with_suffix(p.suffix + ".bak_0x659_v3")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")

def find_fake_das_def_file() -> Path:
  if not TESLA_DIR.exists():
    raise RuntimeError(f"missing dir: {TESLA_DIR}")

  for py in TESLA_DIR.rglob("*.py"):
    s = read_norm(py)
    if re.search(r"^\s*def\s+create_fake_das_msg\s*\(", s, re.M):
      return py
    if re.search(r"^\s*def\s+create_fake_das_message\s*\(", s, re.M):
      return py

  # fallback: find any file mentioning 0x659 and "fake" or "das"
  for py in TESLA_DIR.rglob("*.py"):
    s = read_norm(py)
    if "0x659" in s and ("fake" in s.lower() or "das" in s.lower()):
      return py

  raise RuntimeError("couldn't locate python definition for create_fake_das_msg/create_fake_das_message")

def replace_python_def_block(src: str, def_name: str, new_block: str) -> str:
  """
  Replace a def block (top-level or method) by indentation-aware slicing.
  """
  lines = src.splitlines(True)

  # locate def line
  idx = None
  indent = ""
  for i, line in enumerate(lines):
    m = re.match(r"^(\s*)def\s+" + re.escape(def_name) + r"\s*\(", line)
    if m:
      idx = i
      indent = m.group(1)
      break
  if idx is None:
    return src

  # consume until next line with indent <= def indent that starts a new def/class (or EOF)
  j = idx + 1
  while j < len(lines):
    line = lines[j]
    if line.strip() == "":
      j += 1
      continue
    if not line.startswith(indent):
      break
    # if same indent and starts a new block, stop
    if re.match(r"^" + re.escape(indent) + r"(def|class)\s+", line):
      break
    j += 1

  replaced = "".join(lines[:idx]) + new_block + "".join(lines[j:])
  return replaced

def patch_fake_das_def(py_path: Path) -> tuple[str, str]:
  src0 = read_norm(py_path)
  src = src0

  # Determine which def exists
  has_msg = re.search(r"^\s*def\s+create_fake_das_msg\s*\(", src, re.M) is not None
  has_message = re.search(r"^\s*def\s+create_fake_das_message\s*\(", src, re.M) is not None

  def_name = "create_fake_das_msg" if has_msg else "create_fake_das_message" if has_message else None
  if def_name is None:
    # Insert a new top-level helper if no def exists (rare)
    raise RuntimeError(f"{py_path}: no create_fake_das_* def found to patch")

  # Get indentation from existing def
  m = re.search(r"^(\s*)def\s+" + re.escape(def_name) + r"\s*\(", src, re.M)
  indent = m.group(1) if m else ""

  # New block preserves indent and stays backward compatible: accepts old (pedal, ap_dis, bus) calls.
  new_block = (
    f"{indent}def {def_name}(self, pedalEnabled: bool, autopilot_disabled: bool, bus: int, "
    f"stalk_main: bool = False, stalk_cancel: bool = False):\n"
    f"{indent}    \"\"\"Internal openpilot->panda message (0x659), consumed by tesla_legacy safety.\n"
    f"{indent}    Byte5 bits:\n"
    f"{indent}      bit7: autopilot_disabled\n"
    f"{indent}      bit5: pedalEnabled\n"
    f"{indent}      bit1: stalk_main (edge)\n"
    f"{indent}      bit0: stalk_cancel (edge)\n"
    f"{indent}    Safety tx_hook consumes + blocks it; it never hits the car.\n"
    f"{indent}    \"\"\"\n"
    f"{indent}    dat = bytearray(8)\n"
    f"{indent}    dat[5] = (0x20 if pedalEnabled else 0) | (0x80 if autopilot_disabled else 0) | \\\n"
    f"{indent}             (0x02 if stalk_main else 0) | (0x01 if stalk_cancel else 0)\n"
    f"{indent}    return (0x659, bytes(dat), bus)\n"
  )

  src = replace_python_def_block(src, def_name, new_block)
  if src == src0:
    raise RuntimeError(f"{py_path}: failed to replace {def_name} block")

  return src0, src

def patch_carcontroller() -> tuple[str, str]:
  p = CARCONTROLLER
  if not p.exists():
    raise RuntimeError(f"missing: {p}")

  src0 = read_norm(p)
  src = src0
  if MARK_CC in src:
    return src0, src0

  # Replace the existing 10Hz send block that appends create_fake_das_msg(...) at least once.
  # Works whether the fork sends to bus 0, 4, 128 offsets, etc.
  block_re = re.compile(
    r"(?P<indent>^[ \t]*)if\s*\(\s*\(?\s*self\.frame\s*%\s*10\s*\)?\s*\)\s*==\s*0\s*:\s*\n"
    r"(?:(?P=indent)[ \t]+can_sends\.append\([^\n]*create_fake_das_(?:msg|message)[^\n]*\)\s*\n)+",
    re.M,
  )
  m = block_re.search(src)
  if not m:
    raise RuntimeError("carcontroller.py: couldn't find the existing 10Hz fake-DAS send block")

  ind = m.group("indent")
  new_block = (
    f"{ind}{MARK_CC}\n"
    f"{ind}# Send internal DAS msg (0x659) to BOTH panda bus blocks (party and party+4).\n"
    f"{ind}# Carries stalk MAIN/CANCEL edges so both pandas latch controlsAllowed identically.\n"
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

  src = src[:m.start()] + new_block + src[m.end():]
  return src0, src

def patch_safety() -> tuple[str, str]:
  p = SAFETY
  if not p.exists():
    raise RuntimeError(f"missing: {p}")

  src0 = read_norm(p)
  src = src0
  if MARK_SF in src:
    return src0, src0

  src = ILLEGAL_ASSIGN_RE.sub("", src)

  # Replace the first `if (msg->addr == 0x659U) { ... }` block (broad but safe here).
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

  src = src[:m.start()] + repl + src[m.end():]

  if ILLEGAL_ASSIGN_RE.search(src):
    raise RuntimeError("tesla_legacy.h: illegal file-scope assignment still present after patch")

  return src0, src

def main() -> int:
  fake_def_file = find_fake_das_def_file()
  print(f"Found fake DAS def in: {fake_def_file}")

  tc0 = read_norm(fake_def_file)
  cc0 = read_norm(CARCONTROLLER)
  sf0 = read_norm(SAFETY)

  tc_old, tc_new = patch_fake_das_def(fake_def_file)
  cc_old, cc_new = patch_carcontroller()
  sf_old, sf_new = patch_safety()

  backup_write(fake_def_file, tc_old, tc_new)
  backup_write(CARCONTROLLER, cc_old, cc_new)
  backup_write(SAFETY, sf_old, sf_new)

  print("OK: patched fake-DAS python def, carcontroller.py, tesla_legacy.h (backups: *.bak_0x659_v3)")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
