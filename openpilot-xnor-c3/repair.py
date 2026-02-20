#!/usr/bin/env python3
"""
Repair teslacan.py after patch scripts:
- Ensure tesla_checksum is defined (fixes ImportError on boot)
- Replace create_fake_das_msg() with a syntax-safe version
- Patch both possible locations (opendbc/ and opendbc_repo/)
- Backup originals
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional


CANDIDATES = [
  Path("/data/openpilot/opendbc/car/tesla/teslacan.py"),
  Path("/data/openpilot/opendbc_repo/opendbc/car/tesla/teslacan.py"),
]

TESLA_CHECKSUM_BLOCK = """\
def tesla_checksum(address: int, sig, d: bytearray) -> int:
  checksum = (address & 0xFF) + ((address >> 8) & 0xFF)
  checksum_byte = sig.start_bit // 8
  for i in range(len(d)):
    if i != checksum_byte:
      checksum += d[i]
  return checksum & 0xFF
"""

CRC8_11D_BLOCK = """\
def _crc8_11d(data: bytes) -> int:
  crc = 0xFF
  for b in data:
    crc ^= b
    for _ in range(8):
      crc = ((crc << 1) ^ 0x1D) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
  return crc
"""

INJECT_NOTE = "Byte5 bits: bit7=autopilot_disabled, bit5=pedalEnabled, bit1=stalk_main(edge), bit0=stalk_cancel(edge)"


def _replace_create_fake_das_msg(src: str) -> str:
  # Find the create_fake_das_msg method (works for 2-space or 4-space indents)
  m = re.search(r"^(?P<ind>\s*)def\s+create_fake_das_msg\s*\([^\n]*\)\s*:\s*\n", src, re.M)
  if not m:
    return src

  ind = m.group("ind")
  start = m.start()

  # Find end of this def block: next "def " at same indent level (or end of file)
  after = src[m.end():]
  n = re.search(rf"^{re.escape(ind)}def\s+\w+\s*\(", after, re.M)
  end = m.end() + (n.start() if n else len(after))

  body = (
    f"{ind}def create_fake_das_msg(self, pedalEnabled: bool, autopilot_disabled: bool, "
    f"stalk_main: bool = False, stalk_cancel: bool = False, bus: int = CANBUS.party):\n"
    f'{ind}  """Unity parity: internal openpilot->panda message (0x659) to configure safety.\n'
    f"{ind}  {INJECT_NOTE}\n"
    f"{ind}  Not in any DBC; safety tx_hook consumes and blocks it from hitting the car.\n"
    f'{ind}  """\n'
    f"{ind}  dat = bytearray(8)\n"
    f"{ind}  dat[5] = ((0x20 if pedalEnabled else 0) |\n"
    f"{ind}            (0x80 if autopilot_disabled else 0) |\n"
    f"{ind}            (0x02 if stalk_main else 0) |\n"
    f"{ind}            (0x01 if stalk_cancel else 0))\n"
    f"{ind}  return (0x659, bytes(dat), bus)\n\n"
  )

  return src[:start] + body + src[end:]


def _ensure_checksum(src: str) -> str:
  if "def tesla_checksum" in src:
    return src
  # Append at end (top-level)
  if not src.endswith("\n"):
    src += "\n"
  return src + "\n" + TESLA_CHECKSUM_BLOCK


def _ensure_crc8(src: str) -> str:
  # Only needed if missing; some forks already have it
  if "def _crc8_11d" in src:
    return src
  if not src.endswith("\n"):
    src += "\n"
  return src + "\n" + CRC8_11D_BLOCK


def _normalize_newlines(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")


def patch_file(p: Path) -> Optional[Path]:
  if not p.exists():
    return None

  old = _normalize_newlines(p.read_text(encoding="utf-8", errors="replace"))
  new = old

  new = _replace_create_fake_das_msg(new)
  new = _ensure_crc8(new)
  new = _ensure_checksum(new)

  # Validate syntax
  ast.parse(new, filename=str(p))

  if new == old:
    print(f"OK (no changes): {p}")
    return p

  bak = p.with_suffix(p.suffix + ".bak_repair")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")
  print(f"PATCHED: {p} (backup: {bak})")
  return p


def main() -> int:
  patched_any = False
  for p in CANDIDATES:
    try:
      res = patch_file(p)
      patched_any |= res is not None
    except Exception as e:
      raise SystemExit(f"ERROR patching {p}: {e}")

  if not patched_any:
    raise SystemExit("ERROR: no teslacan.py candidates found")

  print("DONE: teslacan.py repaired")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
