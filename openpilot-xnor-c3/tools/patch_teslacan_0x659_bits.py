#!/usr/bin/env python3
"""
/data/openpilot/tools/patch_teslacan_0x659_bits.py

Extends TeslaCAN.create_fake_das_msg to accept:
  stalk_main=False, stalk_cancel=False

And sets byte5 bits:
  bit7 0x80 autopilot_disabled
  bit5 0x20 pedalEnabled
  bit1 0x02 MAIN
  bit0 0x01 CANCEL

Patches both:
  /data/openpilot/opendbc/car/tesla/teslacan.py
  /data/openpilot/opendbc_repo/opendbc/car/tesla/teslacan.py

Backup: *.bak_0x659_bits
"""
from __future__ import annotations
import re
from pathlib import Path

TARGETS = [
  Path("/data/openpilot/opendbc/car/tesla/teslacan.py"),
  Path("/data/openpilot/opendbc_repo/opendbc/car/tesla/teslacan.py"),
]

DEF_RE = re.compile(
  r'(?m)^(?P<ind>[ \t]*)def\s+create_fake_das_msg\s*\(\s*self\s*,\s*pedalEnabled:\s*bool\s*,\s*autopilot_disabled:\s*bool\s*,\s*bus:\s*int\s*=\s*CANBUS\.party\s*\)\s*:\s*$'
)
DAT_RE = re.compile(r'(?m)^(?P<ind>[ \t]*)dat\[5\]\s*=\s*\(0x20 if pedalEnabled else 0\)\s*\|\s*\(0x80 if autopilot_disabled else 0\)\s*$')

def patch(p: Path) -> bool:
  if not p.exists():
    return False
  s = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  if "stalk_main" in s and "stalk_cancel" in s and "0x02" in s and "0x01" in s:
    print(f"OK: already has stalk bits: {p}")
    return True

  m = DEF_RE.search(s)
  if not m:
    raise RuntimeError(f"{p}: couldn't find TeslaCAN.create_fake_das_msg(self, pedalEnabled, autopilot_disabled, bus=CANBUS.party)")

  ind = m.group("ind")
  new_def = (
    f"{ind}def create_fake_das_msg(self, pedalEnabled: bool, autopilot_disabled: bool, bus: int = CANBUS.party,\n"
    f"{ind}                        stalk_main: bool = False, stalk_cancel: bool = False):"
  )
  s2 = s[:m.start()] + new_def + s[m.end():]

  m2 = DAT_RE.search(s2)
  if not m2:
    raise RuntimeError(f"{p}: couldn't find dat[5] assignment to extend")
  ind2 = m2.group("ind")
  s3 = DAT_RE.sub(
    f"{ind2}dat[5] = (0x20 if pedalEnabled else 0) | (0x80 if autopilot_disabled else 0) | (0x02 if stalk_main else 0) | (0x01 if stalk_cancel else 0)",
    s2, count=1
  )

  bak = p.with_suffix(p.suffix + ".bak_0x659_bits")
  if not bak.exists():
    bak.write_text(s, encoding="utf-8")
  p.write_text(s3, encoding="utf-8")
  print(f"PATCHED: {p} (backup: {bak})")
  return True

def main() -> int:
  any_ = False
  for p in TARGETS:
    if p.exists():
      patch(p); any_ = True
  if not any_:
    raise SystemExit("No teslacan.py found")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
