#!/usr/bin/env python3
"""
panda/scripts/recover_and_flash.py

Robust recovery + flashing helper for comma.ai Panda devices.

What it does
- Builds firmware (same as board/flash.py) unless --no-build.
- Detects Panda USB devices and STM32 DFU devices.
- If DFU devices exist, flashes bootstub (PandaDFU.recover()).
- Attempts to flash app firmware (Panda.flash()) with retries.
- Prints clear status for each step.

Use cases
- Host rebooted mid-flash; Panda now stuck resetting / only briefly enumerates.
- One of multiple pandas "disappears".
- DFU recovery needed.

Notes
- Run from the repo's `panda/` directory: `python3 scripts/recover_and_flash.py`
- If you get permission errors, run with sudo or install udev rules.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from typing import Optional

from panda import Panda, PandaDFU


def _run(cmd: str) -> None:
  print(f"[cmd] {cmd}")
  subprocess.check_call(cmd, shell=True)


def _build_firmware(repo_panda_dir: str) -> None:
  board_path = os.path.join(repo_panda_dir, "board")
  _run(f"scons -C {repo_panda_dir} -j$(nproc) {board_path}")


def _list_pandas() -> list[str]:
  try:
    return Panda.list()
  except Exception as e:
    print(f"[warn] Panda.list() failed: {e}")
    return []


def _list_dfus() -> list[str]:
  try:
    return PandaDFU.list()
  except Exception as e:
    print(f"[warn] PandaDFU.list() failed: {e}")
    return []


def _print_inventory() -> None:
  pandas = _list_pandas()
  dfus = _list_dfus()
  print(f"[info] Panda USB devices: {pandas}")
  print(f"[info] DFU devices:      {dfus}")


def _recover_dfus(timeout_s: int) -> None:
  t0 = time.monotonic()
  while True:
    dfus = _list_dfus()
    if dfus:
      break
    if time.monotonic() - t0 > timeout_s:
      print("[info] No DFU devices detected.")
      return
    time.sleep(0.2)

  for s in dfus:
    print(f"[info] Recovering DFU device {s} (flashing bootstub)...")
    try:
      with PandaDFU(s) as d:
        d.recover()
      print(f"[ok] DFU {s} recovered.")
    except Exception as e:
      print(f"[err] DFU recover failed for {s}: {e}")


def _wait_for_panda(serial: Optional[str], timeout_s: int) -> bool:
  t0 = time.monotonic()
  while time.monotonic() - t0 < timeout_s:
    serials = _list_pandas()
    if serial is None and serials:
      return True
    if serial is not None and serial in serials:
      return True
    time.sleep(0.2)
  return False


def _flash_one(serial: Optional[str], retries: int, settle_s: float) -> bool:
  for attempt in range(1, retries + 1):
    try:
      with Panda(serial=serial) as p:
        usb_serial = p.get_usb_serial()
        hw = p.get_type().hex()
        bootstub = getattr(p, "bootstub", False)
        print(f"[info] Connected: usb_serial={usb_serial} hw={hw} bootstub={bootstub}")

        try:
          print("[info] Flashing app firmware...")
          p.flash()
          print("[ok] Flash succeeded.")
          time.sleep(settle_s)
          return True
        except Exception as e:
          print(f"[warn] Flash attempt {attempt}/{retries} failed: {e}")

          try:
            print("[info] Attempting full recover (bootstub+DFU)...")
            ok = p.recover(timeout=30, reset=True)
            print(f"[info] Recover returned: {ok}")
          except Exception as e2:
            print(f"[warn] Recover failed: {e2}")

    except Exception as e:
      print(f"[warn] Could not connect to panda (attempt {attempt}/{retries}): {e}")

    time.sleep(0.5)

  return False


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--no-build", action="store_true", help="Skip scons build step")
  parser.add_argument("--all", action="store_true", help="Flash all connected pandas")
  parser.add_argument("--serial", default=None, help="Target a specific panda USB serial")
  parser.add_argument("--retries", type=int, default=8, help="Retries per panda")
  parser.add_argument("--settle", type=float, default=1.0, help="Seconds to wait after successful flash")
  parser.add_argument("--dfu-timeout", type=int, default=3, help="Seconds to wait for DFU devices before giving up")
  parser.add_argument("--wait", type=int, default=10, help="Seconds to wait for panda to enumerate")
  args = parser.parse_args()

  repo_panda_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
  os.chdir(repo_panda_dir)

  print("[info] === panda recover_and_flash ===")
  print(f"[info] repo_panda_dir={repo_panda_dir}")
  _print_inventory()

  if not args.no_build:
    _build_firmware(repo_panda_dir)

  _recover_dfus(args.dfu_timeout)
  time.sleep(0.5)

  if args.serial is not None:
    if not _wait_for_panda(args.serial, args.wait):
      print("[err] Target serial not found.")
      print("      If the device is boot-looping, force DFU: unplug -> hold BOOT -> plug -> release BOOT after ~2s.")
      _print_inventory()
      return 2

  if args.all:
    serials = _list_pandas()
    if args.serial is not None:
      serials = [s for s in serials if s == args.serial]
    print(f"[info] Flashing {len(serials)} panda(s): {serials}")
  else:
    serials = [args.serial]  # None means "first available"

  ok_all = True
  for s in serials:
    print(f"[info] === Flash target: {s or 'FIRST_AVAILABLE'} ===")
    ok = _flash_one(s, retries=args.retries, settle_s=args.settle)
    ok_all = ok_all and ok
    if not ok:
      print("[err] Flash failed for this panda.")
      print("      Next actions:")
      print("      1) Try a different USB cable/port (no hub)")
      print("      2) Force DFU (BOOT button) and rerun with --no-build")
      print("      3) Use a powered USB hub if you suspect brownout")
  return 0 if ok_all else 1


if __name__ == "__main__":
  raise SystemExit(main())
