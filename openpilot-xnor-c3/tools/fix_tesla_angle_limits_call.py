#!/usr/bin/env python3
"""
/data/openpilot/tools/fix_tesla_angle_limits_call.py

Fix crash:
  TypeError: apply_std_steer_angle_limits() takes 6 positional arguments but 7 were given

Rewrites Tesla carcontroller call to match current signature:
  apply_std_steer_angle_limits(apply_angle, apply_angle_last, v_ego, steering_angle, lat_active, limits)

Uses:
  limits = CarControllerParams.ANGLE_LIMITS

Patches both trees if present:
  /data/openpilot/opendbc/car/tesla/carcontroller.py
  /data/openpilot/opendbc_repo/opendbc/car/tesla/carcontroller.py

Backup: *.bak_angle_limits_call
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path("/data/openpilot")
TARGETS = [
  ROOT / "opendbc/car/tesla/carcontroller.py",
  ROOT / "opendbc_repo/opendbc/car/tesla/carcontroller.py",
]

# Matches "... lat_active, CarControllerParams, self.VM" across whitespace/newlines
BAD_TAIL_RE = re.compile(
  r"lat_active\s*,\s*CarControllerParams\s*,\s*self\.VM\s*\)",
  flags=re.M,
)

GOOD_TAIL = "lat_active, CarControllerParams.ANGLE_LIMITS)"


def patch_file(p: Path) -> bool:
  if not p.exists():
    return False

  old = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  if "CarControllerParams.ANGLE_LIMITS" in old and not BAD_TAIL_RE.search(old):
    print(f"OK already fixed: {p}")
    return True

  if not BAD_TAIL_RE.search(old):
    raise RuntimeError(f"{p}: did not find the bad call tail (lat_active, CarControllerParams, self.VM)")

  new = BAD_TAIL_RE.sub(GOOD_TAIL, old, count=1)

  bak = p.with_suffix(p.suffix + ".bak_angle_limits_call")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")
  print(f"PATCHED: {p} (backup: {bak})")
  return True


def main() -> int:
  any_done = False
  for p in TARGETS:
    if p.exists():
      patch_file(p)
      any_done = True
  if not any_done:
    raise SystemExit("No target carcontroller.py files found")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
