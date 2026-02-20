#!/usr/bin/env python3
import argparse
import csv
import time
from collections import defaultdict

from panda import Panda


KEY_IDS = {
  0x370, 0x155, 0x45, 0x659,
  0x368, 0x256, 0x108, 0x106, 0x20A, 0x1F8,
  0x399, 0x219,
  0x389, 0x329, 0x349, 0x369,
}


def now_us() -> int:
  return int(time.time() * 1_000_000)


def connect_any(serial: str | None = None) -> Panda:
  while True:
    try:
      return Panda(serial, cli=False)
    except Exception as e:
      print(f"connect failed ({e}), retrying in 1s...")
      time.sleep(1)


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument("--serial", default=None)
  ap.add_argument("--seconds", type=int, default=10)
  ap.add_argument("--csv", default="can_snip.csv")
  args = ap.parse_args()

  p = connect_any(args.serial)
  print("connected:", p.get_serial()[0])

  start = time.time()
  end = start + args.seconds

  hist = defaultdict(int)
  rows = 0

  with open(args.csv, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["t_us", "addr", "bus", "data_hex"])

    while time.time() < end:
      try:
        for addr, dat, src in p.can_recv():
          t = now_us()
          bus = src
          if addr in KEY_IDS:
            hist[(addr, bus)] += 1
          w.writerow([t, hex(addr), bus, dat.hex()])
          rows += 1
      except AssertionError as e:
        # CAN packet checksum incorrect -> reconnect
        print("CAN unpack assertion, reconnecting:", e)
        try:
          p.close()
        except Exception:
          pass
        time.sleep(0.5)
        p = connect_any(args.serial)

  print(f"\nWrote {rows} rows to {args.csv}\n")
  print("Key-ID histogram (addr, bus) -> count")
  for (addr, bus), cnt in sorted(hist.items(), key=lambda x: (x[0][0], x[0][1])):
    print(f"{hex(addr):>6} bus{bus}: {cnt}")


if __name__ == "__main__":
  main()
