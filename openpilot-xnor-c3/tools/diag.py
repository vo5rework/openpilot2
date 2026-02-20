#!/usr/bin/env python3
"""
/data/openpilot/tools/diag.py

Diagnostics via cereal.messaging sockets (no direct Panda USB access; works with openpilot running).

Commands:
  watch  - periodic pandaStates deltas + (optional) alert text changes
  survey - CAN ID survey via the 'can' socket (counts IDs per src), with optional overlap hints

Examples:
  python3 /data/openpilot/tools/diag.py survey --seconds 5 --top 25 --dup-hint
  python3 /data/openpilot/tools/diag.py watch --interval 0.25 --stats-every 2
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import time
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Tuple

from cereal import messaging  # type: ignore


def _as_int(x: Any, default: int = 0) -> int:
  if x is None:
    return default
  if isinstance(x, bool):
    return int(x)
  if isinstance(x, int):
    return x
  raw = getattr(x, "raw", None)  # capnp DynamicEnum
  if raw is not None:
    try:
      return int(raw)
    except Exception:
      return default
  try:
    return int(x)
  except Exception:
    return default


def _as_str(x: Any, default: str = "") -> str:
  if x is None:
    return default
  try:
    return str(x)
  except Exception:
    return default


def _drain(sock) -> Optional[Any]:
  last = None
  while True:
    m = messaging.recv_sock(sock, wait=False)
    if m is None:
      break
    last = m
  return last


def _fmt_hex(x: int) -> str:
  return f"0x{x:x}"


@dataclasses.dataclass
class BusStat:
  rx: int = 0
  tx: int = 0
  err: int = 0
  rec: int = 0
  tec: int = 0
  lec: str = "?"
  irq_rx: int = 0
  irq_tx: int = 0


@dataclasses.dataclass
class PandaStat:
  panda_id: str
  safety_model: int
  safety_param: int
  faults: int
  fault_status: int
  controls_allowed: bool
  rx_checks_invalid: bool
  uptime: int
  buses: Dict[int, BusStat]


def _extract_panda_stats(panda_states_msg: Any) -> List[PandaStat]:
  if panda_states_msg is None:
    return []
  out: List[PandaStat] = []
  for p in getattr(panda_states_msg, "pandaStates", []):
    panda_id = _as_str(getattr(p, "pandaId", getattr(p, "serial", "?")), "?")
    buses: Dict[int, BusStat] = {}
    for idx, ch in enumerate(getattr(p, "canHealth", [])):
      buses[idx] = BusStat(
        rx=_as_int(getattr(ch, "totalRxCnt", 0)),
        tx=_as_int(getattr(ch, "totalTxCnt", 0)),
        err=_as_int(getattr(ch, "totalErrorCnt", 0)),
        rec=_as_int(getattr(ch, "receiveErrorCnt", 0)),
        tec=_as_int(getattr(ch, "transmitErrorCnt", 0)),
        lec=_as_str(getattr(ch, "lastError", "?"), "?"),
        irq_rx=_as_int(getattr(ch, "irq0CallRate", 0)),
        irq_tx=_as_int(getattr(ch, "irq1CallRate", 0)),
      )
    out.append(PandaStat(
      panda_id=panda_id,
      safety_model=_as_int(getattr(p, "safetyModel", 0)),
      safety_param=_as_int(getattr(p, "safetyParam", 0)),
      faults=_as_int(getattr(p, "faults", 0)),
      fault_status=_as_int(getattr(p, "faultStatus", 0)),
      controls_allowed=bool(getattr(p, "controlsAllowed", False)),
      rx_checks_invalid=bool(getattr(p, "rxChecksInvalid", False)),
      uptime=_as_int(getattr(p, "uptime", 0)),
      buses=buses,
    ))
  return out


def _index_by_id(stats: Iterable[PandaStat]) -> Dict[str, PandaStat]:
  return {s.panda_id: s for s in stats}


def _print_delta(prev: PandaStat, cur: PandaStat, can_src_counts: Optional[Dict[int, int]] = None) -> None:
  print(f"== {cur.panda_id} ==")
  print(
    f"uptime={cur.uptime:5d}s safety={cur.safety_model}/{cur.safety_param} "
    f"faults={_fmt_hex(cur.faults)} fault_status={cur.fault_status} "
    f"controlsAllowed={cur.controls_allowed} rxChecksInvalid={cur.rx_checks_invalid}"
  )
  for bus in sorted(cur.buses.keys()):
    p0 = prev.buses.get(bus, BusStat())
    p1 = cur.buses[bus]
    print(
      f"  bus{bus}: +rx {p1.rx - p0.rx:6d} +tx {p1.tx - p0.tx:6d} +err {p1.err - p0.err:6d} "
      f"REC {p1.rec:3d} TEC {p1.tec:3d} LEC {p1.lec:>10} "
      f"irq(rx) {p1.irq_rx:5d} irq(tx) {p1.irq_tx:5d}"
    )
  if can_src_counts:
    top = sorted(can_src_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    if top:
      print("  can(src) top:", ", ".join([f"src{k}={v}" for k, v in top]))
  print()


def cmd_watch(args: argparse.Namespace) -> int:
  ps_sock = messaging.sub_sock("pandaStates", timeout=100)

  # optional: alerts (varies across branches)
  ctrl_sock = None
  sd_sock = None
  try:
    ctrl_sock = messaging.sub_sock("controlsState", timeout=100)
  except Exception:
    ctrl_sock = None
  try:
    sd_sock = messaging.sub_sock("selfdriveState", timeout=100)
  except Exception:
    sd_sock = None

  can_sock = messaging.sub_sock("can", timeout=100) if args.with_can else None

  last_stats_t = time.monotonic() - 1e9
  prev_by_id: Dict[str, PandaStat] = {}
  last_alert = ""

  # accumulate can src counts between prints
  src_counts: Dict[int, int] = {}

  print("Watching (Ctrl+C to stop).")
  print("Note: this prints every --stats-every seconds regardless of alerts.\n")

  try:
    while True:
      t0 = time.monotonic()

      ps_msg = _drain(ps_sock)
      ctrl_msg = _drain(ctrl_sock) if ctrl_sock is not None else None
      sd_msg = _drain(sd_sock) if sd_sock is not None else None

      # alerts (best-effort)
      alert = ""
      if ctrl_msg is not None:
        c = getattr(ctrl_msg, "controlsState", None)
        if c is not None:
          a1 = _as_str(getattr(c, "alertText1", "")).strip()
          a2 = _as_str(getattr(c, "alertText2", "")).strip()
          alert = (a1 + " " + a2).strip()
      if not alert and sd_msg is not None:
        s = getattr(sd_msg, "selfdriveState", None)
        if s is not None:
          a1 = _as_str(getattr(s, "alertText1", "")).strip()
          a2 = _as_str(getattr(s, "alertText2", "")).strip()
          alert = (a1 + " " + a2).strip()

      if alert and alert != last_alert:
        print(f"[alert] '{alert}'")
        last_alert = alert

      # can src counts (best-effort)
      if can_sock is not None:
        while True:
          cm = messaging.recv_sock(can_sock, wait=False)
          if cm is None:
            break
          for m in getattr(cm, "can", []):
            src = _as_int(getattr(m, "src", -1))
            if src >= 0:
              src_counts[src] = src_counts.get(src, 0) + 1

      now = time.monotonic()
      if (now - last_stats_t) >= float(args.stats_every):
        stats = _extract_panda_stats(ps_msg.pandaStates if ps_msg is not None else None)
        cur_by_id = _index_by_id(stats)
        if not prev_by_id:
          prev_by_id = cur_by_id
        for pid, cur in cur_by_id.items():
          prev = prev_by_id.get(pid, cur)
          _print_delta(prev, cur, src_counts if args.with_can else None)
        prev_by_id = cur_by_id
        src_counts = {}
        last_stats_t = now

      dt = time.monotonic() - t0
      time.sleep(max(0.0, float(args.interval) - dt))

  except KeyboardInterrupt:
    print("\nExiting.")
    return 0


def _dup_hint(counts: Dict[int, collections.Counter[int]], topn: int) -> None:
  srcs = sorted(counts.keys())
  top_sets: Dict[int, set[int]] = {s: {a for a, _ in counts[s].most_common(topn)} for s in srcs}
  pairs: List[Tuple[float, int, int, int]] = []
  for i, a in enumerate(srcs):
    for b in srcs[i + 1:]:
      sa, sb = top_sets[a], top_sets[b]
      if not sa or not sb:
        continue
      inter = len(sa & sb)
      denom = max(1, min(len(sa), len(sb)))
      pairs.append((inter / denom, a, b, inter))
  pairs.sort(reverse=True)
  print("dup-hint: top-ID overlap (higher => more likely duplicated traffic):")
  for score, a, b, inter in pairs[:12]:
    if score < 0.6:
      break
    print(f"  src{a} vs src{b}: overlap={inter} score={score:.2f}")
  print()


def cmd_survey(args: argparse.Namespace) -> int:
  can_sock = messaging.sub_sock("can", timeout=100)
  seconds = float(args.seconds)
  topn = int(args.top)

  counts: DefaultDict[int, collections.Counter[int]] = collections.defaultdict(collections.Counter)
  end_t = time.monotonic() + seconds
  while time.monotonic() < end_t:
    msg = messaging.recv_sock(can_sock, wait=True)
    if msg is None:
      continue
    for m in getattr(msg, "can", []):
      addr = _as_int(getattr(m, "address", 0))
      src = _as_int(getattr(m, "src", -1))
      if src >= 0:
        counts[src][addr] += 1

  print(f"Surveyed CAN for {seconds:.1f}s. Top {topn} IDs per src:\n")
  for src in sorted(counts.keys()):
    total = sum(counts[src].values())
    print(f"src{src}: total_msgs={total}")
    for addr, cnt in counts[src].most_common(topn):
      print(f"  0x{addr:03x}: {cnt}")
    print()

  if bool(getattr(args, "dup_hint", False)):
    _dup_hint(counts, topn)

  return 0


def main(argv: Optional[List[str]] = None) -> int:
  parser = argparse.ArgumentParser()
  sub = parser.add_subparsers(dest="cmd", required=True)

  p_watch = sub.add_parser("watch", help="Periodic pandaStates deltas (+ optional alerts)")
  p_watch.add_argument("--interval", type=float, default=0.25)
  p_watch.add_argument("--stats-every", type=float, default=2.0)
  p_watch.add_argument("--with-can", action="store_true", help="Also count can(src) between prints")
  p_watch.set_defaults(func=cmd_watch)

  p_survey = sub.add_parser("survey", help="CAN ID survey via messaging 'can' socket")
  p_survey.add_argument("--seconds", type=float, default=5.0)
  p_survey.add_argument("--top", type=int, default=25)
  p_survey.add_argument("--dup-hint", dest="dup_hint", action="store_true", help="Print overlap hints across src")
  p_survey.set_defaults(func=cmd_survey)

  args = parser.parse_args(argv)
  return int(args.func(args))


if __name__ == "__main__":
  raise SystemExit(main())

