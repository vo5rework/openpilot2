#!/usr/bin/env python3
"""
Ensure Tesla teslacan.py exports module-level create_fake_das_msg/create_fake_das_message.
AST-based (top-level only), so methods inside TeslaCAN won't fool it.

Backups: *.bak_exports_ast
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

EXPORT_BLOCK = """
def create_fake_das_msg(pedal_enabled: bool,
                        autopilot_disabled: bool,
                        bus: int,
                        stalk_main: bool = False,
                        stalk_cancel: bool = False):
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


def create_fake_das_message(pedal_enabled: bool,
                            autopilot_disabled: bool,
                            *,
                            stalk_main: bool = False,
                            stalk_cancel: bool = False,
                            bus: int = 0):
  \"\"\"Compatibility alias for forks expecting create_fake_das_message().\"\"\"
  return create_fake_das_msg(pedal_enabled, autopilot_disabled, bus,
                             stalk_main=stalk_main, stalk_cancel=stalk_cancel)
"""

CLASS_TESLACAN_RE = re.compile(r"^class\s+TeslaCAN\s*:", re.M)


def _norm(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")


def _top_level_names(tree: ast.AST) -> set[str]:
  out: set[str] = set()
  for n in getattr(tree, "body", []):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
      out.add(n.name)
  return out


def _has_method(tree: ast.AST, cls: str, meth: str) -> bool:
  for n in getattr(tree, "body", []):
    if isinstance(n, ast.ClassDef) and n.name == cls:
      for nn in n.body:
        if isinstance(nn, (ast.FunctionDef, ast.AsyncFunctionDef)) and nn.name == meth:
          return True
  return False


def patch_one(p: Path) -> None:
  if not p.exists():
    return

  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  tree = ast.parse(old, filename=str(p))
  top = _top_level_names(tree)

  need_checksum = "tesla_checksum" not in top
  need_msg = "create_fake_das_msg" not in top
  need_message = "create_fake_das_message" not in top

  method_msg = _has_method(tree, "TeslaCAN", "create_fake_das_msg")

  print(f"\n== {p} ==")
  print("top-level has:", sorted([n for n in top if n in {"tesla_checksum","create_fake_das_msg","create_fake_das_message","TeslaCAN"}]))
  print("TeslaCAN.create_fake_das_msg method:", method_msg)

  if not (need_checksum or need_msg or need_message):
    print("OK: module exports already present")
    return

  new = old.rstrip("\n") + "\n\n"
  if need_checksum:
    new += TESLA_CHECKSUM_BLOCK.strip("\n") + "\n\n"
  if need_msg or need_message:
    new += EXPORT_BLOCK.strip("\n") + "\n\n"

  # Prefer inserting before class TeslaCAN if present
  m = CLASS_TESLACAN_RE.search(old)
  if m and (need_checksum or need_msg or need_message):
    inject = ""
    if need_checksum:
      inject += TESLA_CHECKSUM_BLOCK.strip("\n") + "\n\n"
    if need_msg or need_message:
      inject += EXPORT_BLOCK.strip("\n") + "\n\n"
    new = old[:m.start()] + inject + old[m.start():]

  # Validate
  ast.parse(new, filename=str(p))

  bak = p.with_suffix(p.suffix + ".bak_exports_ast")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")
  print(f"PATCHED: wrote module exports (backup: {bak})")


def main() -> int:
  any_found = any(p.exists() for p in TESLACAN_PATHS)
  if not any_found:
    raise SystemExit("No teslacan.py found in expected locations")

  for p in TESLACAN_PATHS:
    patch_one(p)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
