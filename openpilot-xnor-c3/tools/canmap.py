# /data/openpilot/tools/canmap.py
# usage:
#   python3 tools/canmap.py                 # 5s silent sniff
#   python3 tools/canmap.py 5 --silent      # 5s silent sniff
#   python3 tools/canmap.py 3 --split       # 3s sniff with intercept relay engaged (no forwarding)
import sys
import time
from collections import Counter
from panda import Panda

BUS_MASK = 0x7
RETURNED_FLAG = 0x80  # loopback/returned frames in some panda python builds

# Your observed tesla_legacy safety mode is 36.
SAFETY_SILENT = 0
SAFETY_TESLA_LEGACY = 36

# Use param=0x2 in split mode to behave like "external panda" => forwarding disabled in tesla_legacy.h
# and intercept relay is driven by being in a "car safety mode".
TESLA_PARAM_SPLIT_NO_FWD = 0x2

INTEREST_IDS = [
  0x45,   # stalk
  0x155,  # speed
  0x370,  # EPAS status
  0x389,  # DAS failures (patched)
  0x399,  # DAS state (patched)
  0x219,  # EAC/Autopark status
  0x329, 0x349, 0x369,  # warning matrices (zeroed)
]


def set_can_speed(p: Panda, bus: int, kbps: int) -> None:
  if hasattr(p, "set_can_speed_kbps"):
    p.set_can_speed_kbps(bus, kbps)
  elif hasattr(p, "set_can_speed"):
    p.set_can_speed(bus, kbps)
  else:
    raise RuntimeError("Panda python missing set_can_speed_kbps/set_can_speed")


def sniff(p: Panda, seconds: int):
  end = time.monotonic() + seconds
  rx = {0: Counter(), 1: Counter(), 2: Counter()}
  loopback = {0: Counter(), 1: Counter(), 2: Counter()}
  unknown_src = Counter()

  while time.monotonic() < end:
    for addr, dat, src in p.can_recv():
      bus = src & BUS_MASK
      returned = (src & RETURNED_FLAG) != 0

      if bus not in (0, 1, 2):
        unknown_src[src] += 1
        continue

      if returned:
        loopback[bus][addr] += 1
      else:
        rx[bus][addr] += 1

  return rx, loopback, unknown_src


def show(serial: str, rx, loopback, unknown_src) -> None:
  print(f"\n== {serial} ==")
  for bus in (0, 1, 2):
    total = sum(rx[bus].values())
    print(f"\nbus{bus} RX: total={total}")
    for a, n in rx[bus].most_common(12):
      print(f"  0x{a:03x}: {n}")

    lb_total = sum(loopback[bus].values())
    print(f"bus{bus} LOOPBACK: total={lb_total}")
    for a, n in loopback[bus].most_common(6):
      print(f"  0x{a:03x}: {n}")

  # overlap check using RX only
  overlap_02 = len(set(rx[0].keys()) & set(rx[2].keys()))
  only0 = len(set(rx[0].keys()) - set(rx[2].keys()))
  only2 = len(set(rx[2].keys()) - set(rx[0].keys()))
  print(f"\nRX overlap bus0/bus2: {overlap_02}  only0: {only0}  only2: {only2}")

  # print Unity-relevant IDs quickly
  print("\nKey ID counts (RX only):")
  for a in INTEREST_IDS:
    c0 = rx[0].get(a, 0)
    c1 = rx[1].get(a, 0)
    c2 = rx[2].get(a, 0)
    if (c0 + c1 + c2) > 0:
      print(f"  0x{a:03x}: bus0={c0} bus1={c1} bus2={c2}")

  if unknown_src:
    print("\nUnknown src values seen (raw src -> count):")
    for k, v in unknown_src.most_common(10):
      print(f"  {k}: {v}")


def configure(p: Panda, mode: str) -> None:
  if mode == "silent":
    p.set_safety_mode(SAFETY_SILENT, 0)
  elif mode == "split":
    # Engage intercept relay by entering a car safety mode.
    # Use param=0x2 to ensure tesla_legacy disables forwarding (so we only observe wiring).
    p.set_safety_mode(SAFETY_TESLA_LEGACY, TESLA_PARAM_SPLIT_NO_FWD)
  else:
    raise ValueError(mode)

  for b in (0, 1, 2):
    set_can_speed(p, b, 500)


def run_one(serial: str, seconds: int, mode: str) -> bool:
  try:
    p = Panda(serial, cli=False)
  except Exception as e:
    print(f"\n== {serial} ==\n  [error] open failed: {type(e).__name__}: {e}")
    return False

  try:
    configure(p, mode)
    print(f"\n== {serial} ==\n  Sniffing ({mode}) for {seconds}s...")
    rx, loopback, unknown_src = sniff(p, seconds)
    show(serial, rx, loopback, unknown_src)
    return True
  except Exception as e:
    print(f"\n== {serial} ==\n  [error] run failed: {type(e).__name__}: {e}")
    return False
  finally:
    # Always restore SILENT when we’re done.
    try:
      p.set_safety_mode(SAFETY_SILENT, 0)
    except Exception:
      pass
    try:
      p.close()
    except Exception:
      pass


def main() -> None:
  seconds = 5
  mode = "silent"

  for arg in sys.argv[1:]:
    if arg == "--silent":
      mode = "silent"
    elif arg == "--split":
      mode = "split"
    else:
      try:
        seconds = int(arg)
      except ValueError:
        print("usage: python3 tools/canmap.py [seconds] [--silent|--split]")
        raise SystemExit(2)

  serials = sorted(Panda.list())
  print("pandas:", serials)
  if not serials:
    raise SystemExit(1)

  ok_any = False
  for s in serials:
    ok_any |= run_one(s, seconds, mode)

  raise SystemExit(0 if ok_any else 1)


if __name__ == "__main__":
  main()
