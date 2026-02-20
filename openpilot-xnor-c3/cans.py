
#!/usr/bin/env python3

import argparse
import csv
import time
from panda import Panda

def parse_ids(s: str):
  out = set()
  for part in s.split(","):
    part = part.strip().lower()
    if not part:
      continue
    out.add(int(part, 16) if part.startswith("0x") else int(part))
  return out

def main():
  ap = argparse.ArgumentParser(description="Simple CAN sniffer (CSV + optional filters).")
  ap.add_argument("--out", default="can_snip.csv", help="output CSV file")
  ap.add_argument("--ids", default="", help="comma-separated IDs (hex ok), only log these")
  ap.add_argument("--bus", default="", help="comma-separated buses (0,1,2), only log these")
  args = ap.parse_args()

  ids = parse_ids(args.ids) if args.ids else None
  buses = set(int(x) for x in args.bus.split(",")) if args.bus else None

  p = Panda()
  start = time.time()

  with open(args.out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["t", "bus", "addr", "len", "data_hex"])

    print(f"Logging to {args.out}. Ctrl-C to stop.")
    try:
      while True:
        for addr, dat, src in p.can_recv():
          if buses is not None and src not in buses:
            continue
          if ids is not None and addr not in ids:
            continue
          t = time.time() - start
          w.writerow([f"{t:.6f}", src, hex(addr), len(dat), dat.hex()])
    except KeyboardInterrupt:
      pass

if __name__ == "__main__":
  main()
