#!/usr/bin/env python3
"""
/data/openpilot/selfdrive/controls/tesla_fakedasd.py

Unity-parity "HUD module" behavior for XNOR:
- Always publish internal carrier frame 0x659 to sendcan, even when OP disabled.
- Publish on bus 0 and bus 4 so BOTH pandas see the contract.
- Encode MAIN/CANCEL edges based on real stalk 0x45 lever transitions.
- Safety should CONSUME this in tx_hook and BLOCK it from the car.
"""

from __future__ import annotations

import argparse
import time
from typing import Optional, Tuple

from cereal import car, messaging
from openpilot.common.params import Params


ADDR_STALK = 0x45
ADDR_FAKE_DAS = 0x659

BUS_PANDA0 = 0  # panda bus block 0..3
BUS_PANDA1 = 4  # panda bus block 4..7

BIT_AP_DISABLED = 0x80
BIT_PEDAL_ENABLED = 0x20
BIT_MAIN_EDGE = 0x02
BIT_CANCEL_EDGE = 0x01


def lever_position(dat: bytes) -> int:
  # matches your watcher: lower 6 bits of byte0
  return (dat[0] & 0x3F) if dat else -1


def build_659(pedal_enabled: bool, ap_disabled: bool, main_edge: bool, cancel_edge: bool) -> bytes:
  dat = bytearray(8)
  dat[5] = ((BIT_PEDAL_ENABLED if pedal_enabled else 0) |
            (BIT_AP_DISABLED if ap_disabled else 0) |
            (BIT_MAIN_EDGE if main_edge else 0) |
            (BIT_CANCEL_EDGE if cancel_edge else 0))
  return bytes(dat)


def get_carparams_carname(params: Params) -> Optional[str]:
  raw = params.get("CarParams")
  if not raw:
    return None
  try:
    cp = car.CarParams.from_bytes(raw)
    return cp.carName
  except Exception:
    return None


def publish_sendcan(pm: messaging.PubMaster, address: int, dat: bytes, buses: Tuple[int, ...]) -> None:
  msg = messaging.new_message("sendcan", size=len(buses))
  for i, b in enumerate(buses):
    msg.sendcan[i].address = address
    msg.sendcan[i].dat = dat
    msg.sendcan[i].src = b
  pm.send("sendcan", msg)


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--rate_hz", type=float, default=10.0)
  ap.add_argument("--debug", action="store_true")
  args = ap.parse_args()

  params = Params()

  # Don’t spam sendcan on non-Tesla setups
  carname = get_carparams_carname(params)
  if carname is not None and carname != "tesla":
    print(f"tesla_fakedasd: carName={carname!r}, not tesla -> exiting")
    return 0

  pm = messaging.PubMaster(["sendcan"])
  sm = messaging.SubMaster(["can"], ignore_avg_freq=True)

  period = 1.0 / max(args.rate_hz, 1.0)
  next_pub = time.monotonic()

  prev_lever: int = 0
  main_edge = False
  cancel_edge = False

  last_dbg = time.monotonic()

  while True:
    sm.update(0)

    if sm.updated["can"]:
      for m in sm["can"]:
        if m.address != ADDR_STALK:
          continue
        lev = lever_position(bytes(m.dat))
        # MAIN is lever==2 in your logs; CANCEL often lever==1 (if it exists)
        if prev_lever != 2 and lev == 2:
          main_edge = True
        if prev_lever != 1 and lev == 1:
          cancel_edge = True
        if lev >= 0:
          prev_lever = lev

    now = time.monotonic()
    if now >= next_pub:
      ap_disabled = bool(params.get_bool("TinklaAutopilotDisabled"))
      pedal_enabled = bool(params.get_bool("TinklaPedalEnabled"))

      dat = build_659(pedal_enabled, ap_disabled, main_edge, cancel_edge)
      publish_sendcan(pm, ADDR_FAKE_DAS, dat, (BUS_PANDA0, BUS_PANDA1))

      if args.debug and (now - last_dbg) > 1.0:
        b5 = dat[5]
        print(f"[tesla_fakedasd] sent 0x659 buses=[{BUS_PANDA0},{BUS_PANDA1}] b5=0x{b5:02x} lever={prev_lever} main_edge={main_edge} cancel_edge={cancel_edge} ap_dis={ap_disabled} pedal_en={pedal_enabled}")
        last_dbg = now

      main_edge = False
      cancel_edge = False
      next_pub = now + period

    time.sleep(0.005)


if __name__ == "__main__":
  raise SystemExit(main())
