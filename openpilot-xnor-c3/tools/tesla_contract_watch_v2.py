#!/usr/bin/env python3
from __future__ import annotations
import time
from cereal import messaging

ADDR_STALK = 0x45
ADDR_FAKE  = 0x659

def lever_position(dat: bytes) -> int:
  return (dat[0] & 0x3F) if dat else -1

def parse_659(dat: bytes):
  b5 = dat[5] if len(dat) > 5 else 0
  ap_dis = bool(b5 & 0x80)
  ped_en = bool(b5 & 0x20)
  main_edge = bool(b5 & 0x02)
  cancel_edge = bool(b5 & 0x01)
  return b5, ap_dis, ped_en, main_edge, cancel_edge

def _get(obj, name, default=None):
  return getattr(obj, name, default)

sm = messaging.SubMaster(
  ["can", "sendcan", "pandaStates", "selfdriveState", "carState"],
  ignore_avg_freq=True,
)

seen_sendcan = set()
seen_659 = set()
seen_659_edges = []
last_summary = time.monotonic()
last_btn_events = []

print("Watching: can(0x45), sendcan(0x659), pandaStates, carState. Pull stalk. Ctrl+C.\n")

while True:
  sm.update(10)  # 10ms

  if sm.updated.get("can", False):
    for m in sm["can"]:
      if m.address == ADDR_STALK:
        dat = bytes(m.dat)
        lev = lever_position(dat)
        if lev != 0:
          print(f"[can 0x45] bus={m.src} dat={dat.hex()} lever={lev}")

  if sm.updated.get("sendcan", False):
    for m in sm["sendcan"]:
      seen_sendcan.add(m.src)
      if m.address == ADDR_FAKE:
        seen_659.add(m.src)
        b5, apd, ped, main, cancel = parse_659(bytes(m.dat))
        if main or cancel:
          seen_659_edges.append((m.src, b5))
        print(f"[sendcan 0x659] bus={m.src} b5=0x{b5:02x} ap_dis={apd} ped_en={ped} main_edge={main} cancel_edge={cancel}")

  if sm.updated.get("pandaStates", False):
    for i, ps in enumerate(sm["pandaStates"]):
      print(f"[pandaStates[{i}]] safetyModel={ps.safetyModel} safetyParam={ps.safetyParam} controlsAllowed={ps.controlsAllowed} faultStatus={ps.faultStatus}")

  if sm.updated.get("carState", False):
    cs = sm["carState"]
    btn_events = list(_get(cs, "buttonEvents", []))
    if btn_events and btn_events != last_btn_events:
      last_btn_events = btn_events
      evs = ", ".join([f"{be.type}:{be.pressed}" for be in btn_events])
      print(f"[carState] buttonEvents={evs}")

  now = time.monotonic()
  if now - last_summary >= 1.0:
    p0 = sm["pandaStates"][0] if len(sm["pandaStates"]) > 0 else None
    p1 = sm["pandaStates"][1] if len(sm["pandaStates"]) > 1 else None
    cs = sm["carState"] if sm.valid.get("carState", False) else None

    ptxt = ""
    if p0 and p1:
      ptxt = f"p0={p0.controlsAllowed}/{p0.safetyModel}:{p0.safetyParam}:{p0.faultStatus} p1={p1.controlsAllowed}/{p1.safetyModel}:{p1.safetyParam}:{p1.faultStatus}"

    cstxt = ""
    if cs:
      cruise = _get(cs, "cruiseState", None)
      cruise_enabled = _get(cruise, "enabled", None) if cruise else None
      cruise_available = _get(cruise, "available", None) if cruise else None
      v_ego = _get(cs, "vEgo", None)
      gas = _get(cs, "gasPressed", None)
      brake = _get(cs, "brakePressed", None)
      cstxt = f" vEgo={v_ego:.2f} cruise(en={cruise_enabled},avail={cruise_available}) gas={gas} brake={brake}" if v_ego is not None else ""

    edge_txt = ""
    if seen_659_edges:
      edge_txt = f" edges={seen_659_edges}"

    print(f"[summary] sendcan_buses={sorted(seen_sendcan)} seen_0x659_buses={sorted(seen_659)}{edge_txt} {ptxt}{cstxt}")

    last_summary = now
    seen_sendcan.clear()
    seen_659.clear()
    seen_659_edges.clear()
