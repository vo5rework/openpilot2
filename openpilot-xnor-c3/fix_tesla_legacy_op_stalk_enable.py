#!/usr/bin/env python3
"""
Fix Tesla Legacy safety compile error (-Werror=unused-variable) and wire OP_STALK_ENABLE flag properly.

What it does:
- Adds: static bool tesla_legacy_op_stalk_enable = false; (if missing)
- Uses TESLA_FLAG_OP_STALK_ENABLE inside tesla_legacy_init(): tesla_legacy_op_stalk_enable = GET_FLAG(param, TESLA_FLAG_OP_STALK_ENABLE);
- Resets tesla_legacy_op_stalk_enable = false in init reset block (if missing)
- Updates use_stalk_for_controls_allowed to include tesla_legacy_op_stalk_enable

Usage:
  sudo python3 fix_tesla_legacy_op_stalk_enable.py \
    /data/openpilot/opendbc_repo/opendbc/safety/modes/tesla_legacy.h
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def _ensure_global_bool(src: str) -> str:
  if "static bool tesla_legacy_op_stalk_enable" in src:
    return src

  # Insert near the other tesla_legacy_op_* flags if possible.
  anchor = re.search(r"static bool tesla_legacy_op_pedal_enabled.*\n", src)
  if anchor:
    i = anchor.end()
    return src[:i] + "static bool tesla_legacy_op_stalk_enable = false;      // safetyParam bit (Unity stalk contract)\n" + src[i:]

  # Fallback: after first include block.
  anchor = re.search(r'#include "opendbc/safety/safety_declarations\.h"\s*\n', src)
  if not anchor:
    raise RuntimeError("Could not find include anchor")
  i = anchor.end()
  return src[:i] + "\nstatic bool tesla_legacy_op_stalk_enable = false;      // safetyParam bit (Unity stalk contract)\n" + src[i:]


def _wire_flag_in_init(src: str) -> str:
  # Find the const int and ensure we assign the global bool after it.
  pat = re.compile(r"^\s*const int TESLA_FLAG_OP_STALK_ENABLE\s*=\s*32\s*;\s*$", re.M)
  m = pat.search(src)
  if not m:
    # If the const isn't there, create it and wire it in near other flags.
    init_m = re.search(r"static safety_config tesla_legacy_init\s*\(\s*uint16_t param\s*\)\s*\{", src)
    if not init_m:
      raise RuntimeError("Could not find tesla_legacy_init()")
    insert_at = init_m.end()
    inject = "\n  const int TESLA_FLAG_OP_STALK_ENABLE = 32;\n  tesla_legacy_op_stalk_enable = GET_FLAG(param, TESLA_FLAG_OP_STALK_ENABLE);\n"
    return src[:insert_at] + inject + src[insert_at:]

  # If assignment already exists, do nothing.
  if re.search(r"tesla_legacy_op_stalk_enable\s*=\s*GET_FLAG\s*\(\s*param\s*,\s*TESLA_FLAG_OP_STALK_ENABLE\s*\)\s*;", src):
    return src

  # Insert assignment right after the const.
  insert_at = m.end()
  return src[:insert_at] + "\n  tesla_legacy_op_stalk_enable = GET_FLAG(param, TESLA_FLAG_OP_STALK_ENABLE);\n" + src[insert_at:]


def _ensure_reset_in_init(src: str) -> str:
  # Put reset near other resets; idempotent.
  if re.search(r"tesla_legacy_op_stalk_enable\s*=\s*false\s*;", src):
    return src

  # Try to place after other tesla_legacy_op_* resets if present.
  m = re.search(r"tesla_legacy_op_pedal_enabled\s*=\s*false\s*;\s*\n", src)
  if m:
    i = m.end()
    return src[:i] + "  tesla_legacy_op_stalk_enable = false;\n" + src[i:]

  # Fallback: after stock_lkas_prev reset line.
  m = re.search(r"tesla_legacy_stock_lkas_prev\s*=\s*false\s*;\s*\n", src)
  if not m:
    return src
  i = m.end()
  return src[:i] + "  tesla_legacy_op_stalk_enable = false;\n" + src[i:]


def _patch_use_stalk_predicate(src: str) -> str:
  # We want: use_stalk_for_controls_allowed = tesla_legacy_op_stalk_enable || (...) ;
  # Replace common patterns safely.
  patterns = [
    re.compile(r"(const bool use_stalk_for_controls_allowed\s*=\s*)(\(!has_ap_hardware\)\s*\|\|\s*tesla_legacy_op_autopilot_disabled\s*;)", re.M),
    re.compile(r"(const bool use_stalk_for_controls_allowed\s*=\s*)(\(!has_ap_hardware\)\s*\|\|\s*tesla_legacy_op_autopilot_disabled\s*;)", re.M),
  ]
  for pat in patterns:
    if pat.search(src):
      return pat.sub(r"\1tesla_legacy_op_stalk_enable || \2", src, count=1)

  # If the line exists but has a different RHS, prefix it.
  pat2 = re.compile(r"^(?P<indent>\s*)const bool use_stalk_for_controls_allowed\s*=\s*(?P<rhs>.+?);$", re.M)
  m = pat2.search(src)
  if m and "tesla_legacy_op_stalk_enable" not in m.group("rhs"):
    indent = m.group("indent")
    rhs = m.group("rhs").strip()
    return pat2.sub(f"{indent}const bool use_stalk_for_controls_allowed = tesla_legacy_op_stalk_enable || ({rhs});", src, count=1)

  return src


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("path", type=Path)
  args = ap.parse_args()

  p: Path = args.path
  src = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

  before = src
  src = _ensure_global_bool(src)
  src = _wire_flag_in_init(src)
  src = _ensure_reset_in_init(src)
  src = _patch_use_stalk_predicate(src)

  if src == before:
    print("OK: no changes needed")
    return 0

  bak = p.with_suffix(p.suffix + ".bak_op_stalk")
  bak.write_text(before, encoding="utf-8")
  p.write_text(src, encoding="utf-8")
  print(f"FIXED: wrote {p} (backup: {bak})")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
