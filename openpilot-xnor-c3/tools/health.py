#!/usr/bin/env python3
import time
import argparse

try:
  from cereal import messaging
except Exception:
  import cereal.messaging as messaging

def enum_to_int(x):
  # capnp DynamicEnum doesn't cast to int
  return getattr(x, "raw", x)

def find_panda(ps, serial):
  for i, p in enumerate(getattr(ps, "pandaStates", [])):
    if getattr(p, "pandaType", None) is not None:
      pass
    if getattr(p, "pandaSerial", "") == serial:
      return i, p
  return None, None

def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--serial", required=True)
  ap.add_argument("--bus", type=int, default=2)
  ap.add_argument("--interval", type=float, default=0.25)
  ap.add_argument("--seconds", type=float, default=0)
  args = ap.parse_args()

  sm = messaging.SubMaster(["pandaStates"])
  t0 = time.time()

  last = None
  while True:
    sm.update(timeout=1.0)
    ps = sm["pandaStates"]

    idx, p = find_panda(ps, args.serial)
    if p is None:
      print(f"[watch] panda serial {args.serial} not found in pandaStates (have {[getattr(x,'pandaSerial','?') for x in getattr(ps,'pandaStates',[])]})")
      time.sleep(args.interval)
      continue

    safetyModel = enum_to_int(getattr(p, "safetyModel", 0))
    safetyParam = int(getattr(p, "safetyParam", 0))
    controlsAllowed = bool(getattr(p, "controlsAllowed", False))
    rxChecksInvalid = bool(getattr(p, "rxChecksInvalid", False))
    faults = getattr(p, "faults", None)

    ch = getattr(p, "canHealth", [])
    if args.bus >= len(ch):
      print(f"[watch] bus {args.bus} not present (have {len(ch)} buses)")
      time.sleep(args.interval)
      continue

    bh = ch[args.bus]

    # try common field names across versions
    def g(obj, *names, default=0):
      for n in names:
        if hasattr(obj, n):
          return getattr(obj, n)
      return default

    rx = g(bh, "rx", "totalRx", "rxCnt", "rxCount")
    tx = g(bh, "tx", "totalTx", "txCnt", "txCount")
    err = g(bh, "err", "totalErr", "errCnt", "errCount")
    rec = g(bh, "receiveErrorCounter", "rxErrorCounter", "rec", default=0)
    tec = g(bh, "transmitErrorCounter", "txErrorCounter", "tec", default=0)
    lec = g(bh, "lastError", "lec", default=None)

    if last is None:
      drx = dtx = derr = 0
    else:
      drx = rx - last["rx"]
      dtx = tx - last["tx"]
      derr = err - last["err"]
    last = {"rx": rx, "tx": tx, "err": err}

    print(
      f"[{args.serial} idx={idx}] safety={safetyModel}/{safetyParam} "
      f"controlsAllowed={controlsAllowed} rxChecksInvalid={rxChecksInvalid} faults={faults} | "
      f"bus{args.bus}: +rx {drx} +tx {dtx} +err {derr} REC={rec} TEC={tec} LEC={lec}"
    )

    if args.seconds > 0 and (time.time() - t0) > args.seconds:
      break
    time.sleep(args.interval)

if __name__ == "__main__":
  main()
