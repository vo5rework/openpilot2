#!/usr/bin/env python3
"""
Watch Tesla dual-panda contract:
- can 0x45 (STW_ACTN_RQ stalk)
- sendcan 0x659 (internal fake DAS carrier)
- pandaStates controlsAllowed / safetyParam / faultStatus
- selfdriveState

Key output:
- If you never see sendcan 0x659, userspace isn't generating it.
- If you see 0x659 only on one bus, only one panda is getting the carrier.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from cereal import messaging

ADDR_STALK = 0x45
ADDR_FAKE = 0x659


def lever_position(dat: bytes) -> int:
  return (dat[0] & 0x3F) if dat else -1


def parse_659(dat: bytes):
  b5 = dat[5] if len(dat) > 5 else 0
  ap_dis = bool(b5 & 0x80)
  ped_en = bool(b5 & 0x20)
  main_edge = bool(b5 & 0x02)
  cancel_edge = bool(b5 & 0x01)
  return b5, ap_dis, ped_en, main_edge, cancel_edge


def main() -> int:
  sm = messaging.SubMaster(["can", "sendcan", "pandaStates", "selfdriveState"], ignore_avg_freq=True)

  last_summary = time.monotonic()
  seen_659_buses: set[int] = set()
  seen_sendcan_buses: set[int] = set()
  last_pandas = None
  last_sd = None

  print("Watching: can(0x45), sendcan(0x659), pandaStates, selfdriveState. Pull stalk. Ctrl+C to stop.\n")

  while True:
    sm.update(100)

    if sm.updated["can"]:
      for m in sm["can"]:
        if m.address == ADDR_STALK:
          dat = bytes(m.dat)
          lev = lever_position(dat)
          if lev != 0:
            print(f"[can 0x45] bus={m.src} dat={dat.hex()} lever={lev}")

    if sm.updated["sendcan"]:
      for m in sm["sendcan"]:
        seen_sendcan_buses.add(m.src)
        if m.address == ADDR_FAKE:
          seen_659_buses.add(m.src)
          dat = bytes(m.dat)
          b5, apd, ped, main, cancel = parse_659(dat)
          print(f"[sendcan 0x659] bus={m.src} b5=0x{b5:02x} ap_dis={apd} ped_en={ped} main_edge={main} cancel_edge={cancel}")

    if sm.updated["pandaStates"]:
      last_pandas = sm["pandaStates"]
      for i, ps in enumerate(last_pandas):
        print(f"[pandaStates[{i}]] controlsAllowed={ps.controlsAllowed} safetyParam={ps.safetyParam} faultStatus={ps.faultStatus}")

    if sm.updated["selfdriveState"]:
      last_sd = sm["selfdriveState"]
      print(f"[selfdriveState] enabled={last_sd.enabled} active={last_sd.active} state={last_sd.state}")

    now = time.monotonic()
    if now - last_summary >= 1.0:
      # 1Hz summary
      p_summary = ""
      if last_pandas is not None and len(last_pandas) >= 2:
        p0, p1 = last_pandas[0], last_pandas[1]
        p_summary = f"p0_allow={p0.controlsAllowed} p1_allow={p1.controlsAllowed} p0_fault={p0.faultStatus} p1_fault={p1.faultStatus}"
      print(f"[summary] sendcan_buses={sorted(seen_sendcan_buses)} seen_0x659_buses={sorted(seen_659_buses)} {p_summary}")
      last_summary = now
      seen_659_buses.clear()
      seen_sendcan_buses.clear()

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
