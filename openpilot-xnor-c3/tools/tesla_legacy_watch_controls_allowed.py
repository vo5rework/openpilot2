#!/usr/bin/env python3
"""Tesla Legacy: dual-panda contract watcher.

Prints:
  - 0x45 lever_position on each bus
  - 0x1F8 brake_state (if present) and our safety interpretation
  - pandaStates[*].controlsAllowed + safetyParam + (relayMalfunction/faultStatus if present)

Run:
  python3 tools/tesla_legacy_watch_controls_allowed.py
"""

from __future__ import annotations

import time
from typing import Any, Optional

from cereal import messaging
from cereal.services import SERVICE_LIST


ADDR_STALK = 0x45
ADDR_BRAKE = 0x1F8
ADDR_DI_TORQUE1 = 0x106
ADDR_DI_TORQUE1_ALT = 0x108


def bhex(b: bytes) -> str:
  return b.hex()


def lever_position(dat: bytes) -> int:
  return (dat[0] & 0x3F) if dat else -1


def brake_state_from_1f8(dat: bytes) -> int:
  return ((dat[0] >> 2) & 0x03) if dat else -1


def brake_pressed_from_state(state: int) -> bool:
  # Must match tesla_legacy.h: pressed if >= 2
  return state >= 2


def get_field(obj: Any, name: str) -> Optional[Any]:
  return getattr(obj, name) if hasattr(obj, name) else None


def main() -> None:
  subs = ["can", "pandaStates", "selfdriveState"]
  subs = [s for s in subs if s in SERVICE_LIST]
  sm = messaging.SubMaster(subs)
  print("Subscribing:", subs)
  print("Watching... Ctrl+C to stop.")

  last_sd = 0.0
  last_ps = 0.0

  while True:
    sm.update(1000)
    now = time.time()

    if "selfdriveState" in sm.updated and sm.updated["selfdriveState"]:
      sd = sm["selfdriveState"]
      if now - last_sd > 0.25:
        last_sd = now
        print(f"[selfdriveState] enabled={sd.enabled} active={sd.active} state={sd.state}")

    if "pandaStates" in sm.updated and sm.updated["pandaStates"]:
      if now - last_ps > 0.25:
        last_ps = now
        for i, ps in enumerate(sm["pandaStates"]):
          rm = get_field(ps, "relayMalfunction")
          fs = get_field(ps, "faultStatus")
          extras = []
          if rm is not None:
            extras.append(f"relayMalfunction={bool(rm)}")
          if fs is not None:
            extras.append(f"faultStatus={fs}")
          extras_s = (" " + " ".join(extras)) if extras else ""
          print(f"[pandaStates[{i}]] safetyModel={ps.safetyModel} safetyParam={ps.safetyParam} controlsAllowed={ps.controlsAllowed}{extras_s}")

    if "can" in sm.updated and sm.updated["can"]:
      for c in sm["can"]:
        if c.address == ADDR_STALK and c.dat:
          print(f"[can 0x45] bus={c.src} dat={bhex(c.dat)} lever_position={lever_position(c.dat)}")
        elif c.address == ADDR_BRAKE and c.dat:
          st = brake_state_from_1f8(c.dat)
          print(f"[can 0x1F8] bus={c.src} dat={bhex(c.dat)} brake_state={st} pressed={brake_pressed_from_state(st)}")
        elif c.address in (ADDR_DI_TORQUE1, ADDR_DI_TORQUE1_ALT) and c.dat:
          # byte6 visibility; we stopped using this for HW2+external
          b6 = c.dat[6] if len(c.dat) > 6 else 0
          print(f"[can 0x{c.address:X}] bus={c.src} dat={bhex(c.dat)} byte6=0x{b6:02X}")


if __name__ == "__main__":
  main()
