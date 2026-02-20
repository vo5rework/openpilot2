#!/usr/bin/env python3
"""
Repair Tesla opendbc exports so openpilot boots:

1) Ensure tesla_checksum exists in teslacan.py (required by opendbc.can.dbc).
2) Ensure create_fake_das_msg exists (or alias to create_fake_das_message).
3) Make carcontroller import tolerant (try create_fake_das_msg, fallback to create_fake_das_message).

Idempotent. Creates backups: *.bak_fake_das_fix
No openpilot imports; AST parse for syntax validation.
"""

from __future__ import annotations

import ast
import re
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

CREATE_FAKE_DAS_BLOCK = """
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

IMPORT_BLOCK = """\
try:
  from opendbc.car.tesla.teslacan import create_fake_das_msg as _create_fake_das
except ImportError:
  from opendbc.car.tesla.teslacan import create_fake_das_message as _create_fake_das
"""


def _norm(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")


def _backup_write(p: Path, old: str, new: str) -> None:
  if new == old:
    return
  bak = p.with_suffix(p.suffix + ".bak_fake_das_fix")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")


def patch_teslacan(p: Path) -> bool:
  if not p.exists():
    return False

  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  new = old

  if "def tesla_checksum" not in new:
    new = new.rstrip("\n") + "\n\n" + TESLA_CHECKSUM_BLOCK.strip("\n") + "\n"

  # If file already has create_fake_das_message, just alias it.
  if "def create_fake_das_msg" not in new:
    if "def create_fake_das_message" in new and "create_fake_das_msg =" not in new:
      new = new.rstrip("\n") + "\n\ncreate_fake_das_msg = create_fake_das_message\n"
    else:
      # Insert helper before class TeslaCAN if present, else append to end.
      m_cls = re.search(r"^class\s+TeslaCAN\s*:", new, re.M)
      if m_cls:
        new = new[:m_cls.start()] + CREATE_FAKE_DAS_BLOCK.strip("\n") + "\n\n" + new[m_cls.start():]
      else:
        new = new.rstrip("\n") + "\n\n" + CREATE_FAKE_DAS_BLOCK.strip("\n") + "\n"

  ast.parse(new, filename=str(p))
  _backup_write(p, old, new)
  print(f"OK: patched teslacan exports: {p}")
  return True


def patch_carcontroller(p: Path) -> bool:
  if not p.exists():
    return False

  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  new = old

  # Remove any direct imports of create_fake_das_msg/create_fake_das_message
  new = re.sub(r"^\s*from\s+opendbc\.car\.tesla\.teslacan\s+import\s+create_fake_das_msg\s*\n", "", new, flags=re.M)
  new = re.sub(r"^\s*from\s+opendbc\.car\.tesla\.teslacan\s+import\s+create_fake_das_message\s*\n", "", new, flags=re.M)

  # If already injected, skip
  if "_create_fake_das" in new and "except ImportError" in new and "create_fake_das" in new:
    ast.parse(new, filename=str(p))
    _backup_write(p, old, new)
    print(f"OK: carcontroller already tolerant: {p}")
    return True

  # Insert import block after initial imports
  lines = new.splitlines(True)
  ins = 0
  for i, line in enumerate(lines):
    if line.startswith("import ") or line.startswith("from "):
      ins = i + 1
    elif i > 40:
      break
  lines.insert(ins, IMPORT_BLOCK + "\n")
  new = "".join(lines)

  # Replace calls: create_fake_das_msg(...) or create_fake_das_message(...) -> _create_fake_das(...)
  new = new.replace("create_fake_das_msg(", "_create_fake_das(")
  new = new.replace("create_fake_das_message(", "_create_fake_das(")

  ast.parse(new, filename=str(p))
  _backup_write(p, old, new)
  print(f"OK: patched carcontroller import/calls: {p}")
  return True


def main() -> int:
  any_tc = False
  any_cc = False

  for p in TESLACAN_PATHS:
    any_tc |= patch_teslacan(p)

  for p in CARCONTROLLER_PATHS:
    any_cc |= patch_carcontroller(p)

  if not any_tc:
    raise SystemExit("No teslacan.py patched (paths missing)")
  if not any_cc:
    raise SystemExit("No carcontroller.py patched (paths missing)")

  print("DONE")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
