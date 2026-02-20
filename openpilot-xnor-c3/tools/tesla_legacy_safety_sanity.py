#!/usr/bin/env python3
"""Sanity checks for Tesla legacy safety changes.

Fails if:
  - a bare assignment to tesla_legacy_op_autopilot_disabled exists at file scope
  - 0x45 isn't present in tesla_legacy_pt_rx_checks for BOTH bus 0 and bus 2

Run:
  python3 tools/tesla_legacy_safety_sanity.py /data/openpilot/opendbc_repo/opendbc/safety/modes/tesla_legacy.h
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
  if len(sys.argv) != 2:
    print("usage: tesla_legacy_safety_sanity.py <tesla_legacy.h>")
    return 2

  p = Path(sys.argv[1])
  s = p.read_text(encoding="utf-8", errors="replace")

  bad = re.search(r"^\s*tesla_legacy_op_autopilot_disabled\s*=\s*(true|false)\s*;\s*$", s, flags=re.MULTILINE)
  if bad:
    print("FAIL: found bare assignment (missing type) to tesla_legacy_op_autopilot_disabled:")
    print(f"  line: {bad.group(0).strip()!r}")
    return 1

  # Extract pt rx checks block
  m = re.search(r"static\s+RxCheck\s+tesla_legacy_pt_rx_checks\[\]\s*=\s*\{([\s\S]*?)\n\s*\};", s)
  if not m:
    print("FAIL: could not find tesla_legacy_pt_rx_checks[] block")
    return 1

  block = m.group(1)
  have_bus0 = "  {0x45, 0, 8," in block or "{0x45, 0, 8" in block
  have_bus2 = "  {0x45, 2, 8," in block or "{0x45, 2, 8" in block

  if not have_bus0 or not have_bus2:
    print("FAIL: missing 0x45 in tesla_legacy_pt_rx_checks for required buses.")
    print(f"  have bus0={have_bus0}, have bus2={have_bus2}")
    return 1

  print("OK: tesla_legacy.h sanity checks passed")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
