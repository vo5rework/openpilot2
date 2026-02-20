#!/usr/bin/env python3
# /data/openpilot/tools/tesla_contract_watch.py

import time
from cereal import messaging

ADDR_STALK = 0x45
ADDR_659 = 0x659

def lever_position(dat: bytes) -> int:
  return (dat[0] & 0x3F) if dat else -1

def parse_b5(dat: bytes):
  b5 = dat[5] if len(dat) > 5 else 0
  return b5, bool(b5 & 0x80), bool(b5 & 0x20), bool(b5 & 0x02), bool(b5 & 0x01)

def main():
  sm = messaging.SubMaster(["can", "sendcan", "pandaStates", "selfdriveState"], ignore_avg_freq=True)
  last_print = time.time()
  seen_659 = 0

  print("Watching: can(0x45), sendcan(0x659), pandaStates, selfdriveState. Pull stalk. Ctrl+C to stop.\n")

  while True:
    sm.update(100)

    if sm.updated["selfdriveState"]:
      s = sm["selfdriveState"]
      print(f"[selfdriveState] enabled={s.enabled} active={s.active} state={s.state}")

    if sm.updated["can"]:
      for m in sm["can"]:
        if m.address == ADDR_STALK:
          dat = bytes(m.dat)
          print(f"[can 0x45] bus={m.src} lever={lever_position(dat)}")

    if sm.updated["sendcan"]:
      for m in sm["sendcan"]:
        if m.address == ADDR_659:
          seen_659 += 1
          dat = bytes(m.dat)
          b5, ap_dis, ped_en, main_edge, cancel_edge = parse_b5(dat)
          print(f"[sendcan 0x659] bus={m.src} b5=0x{b5:02x} ap_dis={ap_dis} ped={ped_en} main_edge={main_edge} cancel_edge={cancel_edge}")

    if sm.updated["pandaStates"]:
      ps = sm["pandaStates"]
      for i, p in enumerate(ps):
        print(f"[p{i}] allow={p.controlsAllowed} param={p.safetyParam} fault={p.faultStatus}")

    if time.time() - last_print > 1.0:
      last_print = time.time()
      p0 = sm["pandaStates"][0] if len(sm["pandaStates"]) > 0 else None
      p1 = sm["pandaStates"][1] if len(sm["pandaStates"]) > 1 else None
      print(f"[summary] seen_0x659={seen_659} p0_allow={getattr(p0,'controlsAllowed',None)} p1_allow={getattr(p1,'controlsAllowed',None)}")

if __name__ == "__main__":
  main()
