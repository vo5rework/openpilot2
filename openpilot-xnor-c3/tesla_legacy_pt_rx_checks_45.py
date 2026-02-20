#!/usr/bin/env python3
"""
Ensure TeslaLegacy external-panda config can latch controlsAllowed from stalk.

Adds STW_ACTN_RQ (0x45) to tesla_legacy_pt_rx_checks on bus 0 and bus 2.
This is required because external-panda HW2/HW3 uses tesla_legacy_pt_rx_checks.

Usage:
  sudo python3 patch_pt_rx_45.py /data/openpilot/opendbc_repo/opendbc/safety/modes/tesla_legacy.h
"""

from __future__ import annotations
import argparse
import re
from pathlib import Path


RX0 = "    {.msg = {{0x45, 0, 8, 10U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, { 0 }, { 0 }}},  // STW_ACTN_RQ (stalk)\n"
RX2 = "    {.msg = {{0x45, 2, 8, 10U, .ignore_quality_flag = true, .ignore_checksum = true, .ignore_counter = true}, { 0 }, { 0 }}},  // STW_ACTN_RQ (stalk bus2)\n"

def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("path", type=Path)
  args = ap.parse_args()

  p: Path = args.path
  s = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

  # locate tesla_legacy_pt_rx_checks array
  m = re.search(r"(static RxCheck tesla_legacy_pt_rx_checks\[\]\s*=\s*\{\s*\n)([\s\S]*?)(\n\s*\};)", s)
  if not m:
    raise SystemExit("ERROR: couldn't find tesla_legacy_pt_rx_checks[] block")

  head, body, tail = m.group(1), m.group(2), m.group(3)

  changed = False
  if re.search(r"\{\{0x45,\s*0,\s*8,", body) is None:
    body = RX0 + body
    changed = True
  if re.search(r"\{\{0x45,\s*2,\s*8,", body) is None:
    # put bus2 right after bus0 if we inserted it, else prepend
    if RX0 in body:
      body = body.replace(RX0, RX0 + RX2, 1)
    else:
      body = RX2 + body
    changed = True

  if not changed:
    print("OK: 0x45 already present in tesla_legacy_pt_rx_checks")
    return 0

  out = s[:m.start()] + head + body + tail + s[m.end():]

  # absolute guard: no illegal file-scope assignments
  bad = re.search(r"^\s*tesla_legacy_op_(autopilot_disabled|pedal_enabled)\s*=\s*false\s*;\s*$", out, re.M)
  if bad:
    raise SystemExit(f"ERROR: illegal file-scope assignment still present at: {bad.group(0)!r}")

  bak = p.with_suffix(p.suffix + ".bak_pt45")
  bak.write_text(s, encoding="utf-8")
  p.write_text(out, encoding="utf-8")
  print(f"FIXED: added 0x45 to tesla_legacy_pt_rx_checks (backup: {bak})")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
