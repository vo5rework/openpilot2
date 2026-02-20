#!/usr/bin/env python3
"""
tools/tesla_eps_capture.py

Capture Tesla EPS + steering-control related CAN frames from openpilot's messaging
bus (NOT USB). Safe to run while boardd/pandad are active.

Default capture set:
  - 0x370 EPAS3S_sysStatus
  - 0x488 DAS_steeringControl
  - 0x27D APS_eacMonitor
  - 0x659 internal contract (raw decode of byte5 bits)

Usage examples:
  # Run and print decoded values; engage OP while running:
  python3 tools/tesla_eps_capture.py

  # Filter to a specific CAN src (bus) if you know it (try 0/1/2/3/4):
  python3 tools/tesla_eps_capture.py --src 2

  # Capture around engagement edge into a JSONL file:
  python3 tools/tesla_eps_capture.py --out /data/media/0/eps_capture.jsonl --pre 3 --post 6
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

# Prefer cereal.messaging (openpilot standard)
try:
  from cereal import messaging
except Exception as e:
  print(f"ERROR: cannot import cereal.messaging: {e}", file=sys.stderr)
  sys.exit(2)

# Optional DBC decode via opendbc CANParser (will fall back to raw if unavailable)
CANParser = None
try:
  from opendbc.can.parser import CANParser as _CANParser  # type: ignore
  CANParser = _CANParser
except Exception:
  CANParser = None


DEFAULT_IDS = [0x370, 0x488, 0x27D, 0x659]


@dataclass(frozen=True)
class CanFrame:
  mono_time_ns: int
  address: int
  src: int
  dat_hex: str


def _hex(x: int) -> str:
  return f"0x{x:03X}"


def _now_ns() -> int:
  return time.monotonic_ns()


def _dat_to_hex(dat: bytes) -> str:
  return dat.hex()


def _parse_ids(ids_csv: str) -> List[int]:
  out: List[int] = []
  for token in ids_csv.split(","):
    t = token.strip().lower()
    if not t:
      continue
    out.append(int(t, 16) if t.startswith("0x") else int(t))
  return out


def _decode_0x659_byte5(dat: bytes) -> Optional[Dict[str, Any]]:
  # Your controller docstring: byte5 bits are used: bit7 autopilot_disabled, bit5 pedal_enabled, bit1 main_edge, bit0 cancel_edge 1
  if len(dat) < 6:
    return None
  b5 = dat[5]
  return {
    "byte5": b5,
    "autopilot_disabled": bool(b5 & (1 << 7)),
    "pedal_enabled": bool(b5 & (1 << 5)),
    "main_edge": bool(b5 & (1 << 1)),
    "cancel_edge": bool(b5 & (1 << 0)),
  }


def _safe_get(obj: Any, path: Sequence[str], default: Any = None) -> Any:
  cur = obj
  for p in path:
    if cur is None:
      return default
    if hasattr(cur, p):
      cur = getattr(cur, p)
    else:
      return default
  return cur


def _try_make_parser(dbc_name: str, bus: int) -> Optional[Any]:
  if CANParser is None:
    return None
  try:
    # messages: list[(name_or_addr, freq)]
    msgs = [
      ("EPAS3S_sysStatus", 50),
      ("DAS_steeringControl", 50),
      ("APS_eacMonitor", 50),
    ]
    return CANParser(dbc_name, msgs, bus)  # type: ignore[misc]
  except Exception:
    return None


def _format_eps(parser: Any) -> str:
  # Values live in parser.vl['msg_name'] or parser.vl[address]
  vl = parser.vl.get("EPAS3S_sysStatus", {})
  # Keep just the essentials
  keys = [
    "EPAS3S_eacStatus",
    "EPAS3S_eacErrorCode",
    "EPAS3S_handsOnLevel",
    "EPAS3S_torsionBarTorque",
    "EPAS3S_internalSAS",
    "EPAS3S_internalSASQF",
    "EPAS3S_steeringFault",
    "EPAS3S_steeringReduced",
  ]
  parts = []
  for k in keys:
    if k in vl:
      parts.append(f"{k.split('EPAS3S_')[-1]}={vl[k]:.3g}")
  return "EPAS " + " ".join(parts) if parts else "EPAS (no decode)"


def _format_steer(parser: Any) -> str:
  vl = parser.vl.get("DAS_steeringControl", {})
  keys = ["DAS_steeringControlType", "DAS_steeringAngleRequest", "DAS_steeringHapticRequest"]
  parts = []
  for k in keys:
    if k in vl:
      short = k.split("DAS_")[-1]
      v = vl[k]
      parts.append(f"{short}={v:.3g}")
  return "STEER " + " ".join(parts) if parts else "STEER (no decode)"


def _format_allow(parser: Any) -> str:
  vl = parser.vl.get("APS_eacMonitor", {})
  if "APS_eacAllow" in vl:
    return f"ALLOW APS_eacAllow={vl['APS_eacAllow']:.3g}"
  return "ALLOW (no decode)"


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--src", type=int, default=None, help="Filter CAN by src (bus). If omitted, show any.")
  ap.add_argument("--ids", type=str, default=",".join(_hex(x) for x in DEFAULT_IDS),
                  help="Comma-separated CAN IDs (hex like 0x370). Default: 0x370,0x488,0x27D,0x659")
  ap.add_argument("--pre", type=float, default=3.0, help="Seconds of pre-buffer to keep (for engage dump).")
  ap.add_argument("--post", type=float, default=6.0, help="Seconds to capture after engagement edge.")
  ap.add_argument("--duration", type=float, default=0.0, help="Stop after N seconds (0 = run forever).")
  ap.add_argument("--out", type=str, default="", help="Write JSONL frames to this path (optional).")
  ap.add_argument("--dbc", type=str, default="tesla_model3_party", help="DBC name for decode (best-effort).")
  args = ap.parse_args()

  want_ids = set(_parse_ids(args.ids))
  pre_ns = int(args.pre * 1e9)
  post_ns = int(args.post * 1e9)
  stop_ns = int(args.duration * 1e9) if args.duration and args.duration > 0 else 0

  sm = messaging.SubMaster(["can", "controlsState", "carState"])

  # Ring buffer for “engagement-edge” dump
  ring: Deque[CanFrame] = deque()

  out_f = None
  if args.out:
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    out_f = open(args.out, "a", buffering=1)

  last_print_ns = 0
  last_enabled = False
  engage_ns: Optional[int] = None

  # If user provided src, decode using that bus; otherwise try bus 0 for decode and still print raw for others.
  decode_bus = args.src if args.src is not None else 0
  parser = _try_make_parser(args.dbc, decode_bus)

  print("tesla_eps_capture: running (no USB).")
  print(f"  ids={','.join(_hex(i) for i in sorted(want_ids))} src_filter={args.src} decode_bus={decode_bus} dbc={args.dbc} parser={'yes' if parser else 'no'}")
  print("  Engage OP while this runs; it will print updates and dump around engagement edge.\n")

  start_ns = _now_ns()
  while True:
    sm.update(0)

    now_ns = _now_ns()
    if stop_ns and (now_ns - start_ns) >= stop_ns:
      break

    enabled = bool(_safe_get(sm, ["controlsState", "enabled"], False))
    standstill = bool(_safe_get(sm, ["carState", "standstill"], False))
    brake = bool(_safe_get(sm, ["carState", "brakePressed"], False))
    v_ego = _safe_get(sm, ["carState", "vEgo"], None)

    # Rising edge of engagement
    if (not last_enabled) and enabled:
      engage_ns = now_ns
      print(f"\n=== ENGAGE EDGE @ {now_ns/1e9:.3f}s mono === standstill={standstill} brake={brake} vEgo={v_ego}\n")

    last_enabled = enabled

    # Collect relevant CAN frames from messaging
    if sm.updated["can"]:
      t_can = int(sm.logMonoTime["can"]) if sm.logMonoTime["can"] else now_ns
      frames_for_parser: List[Tuple[int, bytes, int]] = []

      for c in sm["can"]:
        addr = int(c.address)
        src = int(c.src)
        if addr not in want_ids:
          continue
        if args.src is not None and src != args.src:
          continue

        dat = bytes(c.dat)
        f = CanFrame(mono_time_ns=t_can, address=addr, src=src, dat_hex=_dat_to_hex(dat))

        ring.append(f)
        # trim ring by time window
        while ring and (t_can - ring[0].mono_time_ns) > pre_ns:
          ring.popleft()

        if out_f is not None:
          out_f.write(json.dumps({"t_ns": f.mono_time_ns, "addr": addr, "src": src, "dat": f.dat_hex}) + "\n")

        # Feed decoder if bus matches
        if parser is not None:
          frames_for_parser.append((addr, dat, src))

        # Lightweight live print for raw 0x659 (not in DBC)
        if addr == 0x659:
          dec = _decode_0x659_byte5(dat)
          if dec is not None:
            print(f"[{t_can/1e9:10.3f}] src={src} 0x659 byte5={dec['byte5']:02x} "
                  f"ap_disabled={int(dec['autopilot_disabled'])} pedal_en={int(dec['pedal_enabled'])} "
                  f"main_edge={int(dec['main_edge'])} cancel_edge={int(dec['cancel_edge'])}")

      # Update parser
      if parser is not None and frames_for_parser:
        try:
          parser.update([(t_can, frames_for_parser)])
        except Exception:
          # Decoder is best-effort; never crash capture
          parser = None

    # Print decoded snapshot at ~5Hz (or immediately after engage)
    should_print = (now_ns - last_print_ns) > int(0.2 * 1e9)
    if engage_ns is not None and (now_ns - engage_ns) < int(1.5 * 1e9):
      should_print = True

    if should_print:
      last_print_ns = now_ns
      hdr = f"[{now_ns/1e9:10.3f}] enabled={int(enabled)} standstill={int(standstill)} brake={int(brake)} vEgo={v_ego}"
      if parser is not None:
        print(hdr)
        print("  " + _format_eps(parser))
        print("  " + _format_allow(parser))
        print("  " + _format_steer(parser))
      else:
        print(hdr)

    # If engaged and post window elapsed, dump ring buffer once and exit
    if engage_ns is not None and (now_ns - engage_ns) >= post_ns:
      print("\n=== POST WINDOW COMPLETE ===")
      print(f"Dumping {len(ring)} pre-buffer frames (last {args.pre:.1f}s before/after engage) to stdout summary.")
      # Summarize last-seen for key IDs
      last_by_id: Dict[int, CanFrame] = {}
      for f in ring:
        last_by_id[f.address] = f
      for addr in sorted(last_by_id.keys()):
        f = last_by_id[addr]
        print(f"  last {_hex(addr)} src={f.src} t={f.mono_time_ns/1e9:.3f} dat={f.dat_hex}")
      break

  if out_f is not None:
    out_f.close()
    print(f"\nWrote JSONL to: {args.out}")

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
