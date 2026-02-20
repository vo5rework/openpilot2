#!/usr/bin/env python3
"""
can_top.py - Show top CAN IDs on a specific panda serial + bus using messaging (no USB claim).

It subscribes to:
  - pandaStates (to map serial -> can src offset)
  - can (to count frames)
"""

import argparse
import time
from collections import Counter
from typing import Optional

import cereal.messaging as messaging


def get_can_offset_for_serial(ps_msg, serial: str) -> Optional[int]:
  """
  Best-effort mapping from panda serial to can src offset.

  Many openpilot builds encode CAN src as:
    src = canBusOffset + bus
  where canBusOffset is often (panda_index << 7).

  This function returns canBusOffset if present, else falls back to (index<<7).
  """
  states = getattr(ps_msg, "pandaStates", [])
  for i, p in enumerate(states):
    if str(getattr(p, "serial", "")) != serial:
      continue
    for attr in ("canBusOffset", "busOffset", "can_bus_offset"):
      off = getattr(p, attr, None)
      if off is not None:
        try:
          return int(off)
        except Exception:
          pass
    return i << 7
  return None


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--serial", required=True, help="Panda serial to watch")
  ap.add_argument("--bus", type=int, default=2, help="Bus number (0/1/2). Default: 2")
  ap.add_argument("--seconds", type=float, default=30.0, help="Total run time. Default: 30")
  ap.add_argument("--window", type=float, default=5.0, help="Rolling window for top IDs. Default: 5")
  ap.add_argument("--top", type=int, default=20, help="How many IDs to show. Default: 20")
  args = ap.parse_args()

  sm = messaging.SubMaster(["pandaStates", "can"])
  t0 = time.monotonic()

  # Resolve src value for this panda+bus
  can_offset = None
  while can_offset is None and (time.monotonic() - t0) < 10.0:
    sm.update(timeout=1000)
    can_offset = get_can_offset_for_serial(sm["pandaStates"], args.serial)
    if can_offset is None:
      have = [str(getattr(p, "serial", "")) for p in getattr(sm["pandaStates"], "pandaStates", [])]
      print(f"[can_top] waiting for pandaStates; have={have}")
  if can_offset is None:
    print(f"[can_top] failed to resolve can src offset for serial {args.serial}")
    return 1

  target_src = can_offset + args.bus
  print(f"[can_top] serial={args.serial} bus={args.bus} -> target src={target_src} (offset={can_offset})")

  counts = Counter()
  window_start = time.monotonic()

  while True:
    now = time.monotonic()
    if (now - t0) > args.seconds:
      break

    sm.update(timeout=1000)

    # can message can contain a list of frames
    msg = sm["can"]
    frames = getattr(msg, "can", [])  # common schema: msg.can is a list
    for fr in frames:
      try:
        if int(getattr(fr, "src", -1)) != target_src:
          continue
        addr = int(getattr(fr, "address", getattr(fr, "addr", -1)))
        if addr >= 0:
          counts[addr] += 1
      except Exception:
        continue

    if (now - window_start) >= args.window:
      total = sum(counts.values())
      print(f"\n[can_top] {args.window:.1f}s window total_frames={total} (src={target_src})")
      for addr, n in counts.most_common(args.top):
        print(f"  0x{addr:03x}: {n}")
      counts.clear()
      window_start = now

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
