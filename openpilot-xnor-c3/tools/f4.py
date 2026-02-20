#!/usr/bin/env python3
"""
f4_can2_watch_v4.py

Watch a specific panda's CAN bus health (esp. F4 CAN2) without grabbing USB by default.
- Default mode: messaging (reads pandaStates via boardd -> avoids LIBUSB busy).
- Optional --usb: connects directly to panda over USB (may conflict with boardd).

Examples:
  # Preferred (no USB busy). Leave openpilot running:
  python3 tools/f4.py --serial 2d0054001051323430373133 --bus 2 --interval 0.25

  # If you killed boardd/pandad and pandaStates is empty:
  python3 tools/f4.py --serial 2d0054001051323430373133 --bus 2 --usb

  # Dump available keys/fields (helpful for debugging schema differences):
  python3 tools/f4.py --serial 2d0054001051323430373133 --bus 2 --dump-keys
"""
from __future__ import annotations

import argparse
import time
from typing import Any, Dict, Optional, Tuple

def _norm_serial(s: Any) -> str:
  if s is None:
    return ""
  if isinstance(s, (bytes, bytearray)):
    try:
      return s.decode("utf-8", "ignore").strip()
    except Exception:
      return str(s)
  return str(s).strip()

def _safe_get(obj: Any, name: str, default: Any = None) -> Any:
  """Get attribute/field from capnp struct or dict, without raising."""
  if obj is None:
    return default
  if isinstance(obj, dict):
    return obj.get(name, default)
  # capnp: getattr can raise AttributeError with schema message; swallow it
  try:
    return getattr(obj, name)
  except Exception:
    return default

def _first_present(obj: Any, candidates: Tuple[str, ...], default: Any = None) -> Any:
  for c in candidates:
    v = _safe_get(obj, c, None)
    if v is not None:
      return v
  return default

def _extract_serial(panda_state: Any) -> str:
  # Different openpilot forks/schemas use different field names
  s = _first_present(panda_state, ("serial", "pandaId", "pandaID", "panda_id", "panda_id_str"), "")
  return _norm_serial(s)

def _extract_safety(panda_state: Any) -> Tuple[Optional[int], Optional[int]]:
  sm = _first_present(panda_state, ("safetyModel", "safety_model"), None)
  sp = _first_present(panda_state, ("safetyParam", "safety_param"), None)

  # capnp enums sometimes aren't ints
  try:
    sm_i = int(sm) if sm is not None else None
  except Exception:
    sm_i = None
  try:
    sp_i = int(sp) if sp is not None else None
  except Exception:
    sp_i = None
  return sm_i, sp_i

_CAN_HEALTH_MAP = {
  "bus_off": ("bus_off", "busOff"),
  "bus_off_cnt": ("bus_off_cnt", "busOffCnt"),
  "error_warning": ("error_warning", "errorWarning"),
  "error_passive": ("error_passive", "errorPassive"),
  "last_error": ("last_error", "lastError"),
  "last_stored_error": ("last_stored_error", "lastStoredError"),
  "last_data_error": ("last_data_error", "lastDataError"),
  "last_data_stored_error": ("last_data_stored_error", "lastDataStoredError"),
  "receive_error_cnt": ("receive_error_cnt", "receiveErrorCnt"),
  "transmit_error_cnt": ("transmit_error_cnt", "transmitErrorCnt"),
  "total_error_cnt": ("total_error_cnt", "totalErrorCnt"),
  "total_tx_lost_cnt": ("total_tx_lost_cnt", "totalTxLostCnt"),
  "total_rx_lost_cnt": ("total_rx_lost_cnt", "totalRxLostCnt"),
  "total_tx_cnt": ("total_tx_cnt", "totalTxCnt"),
  "total_rx_cnt": ("total_rx_cnt", "totalRxCnt"),
  "total_fwd_cnt": ("total_fwd_cnt", "totalFwdCnt"),
  "can_speed": ("can_speed", "canSpeed"),
  "can_data_speed": ("can_data_speed", "canDataSpeed"),
  "canfd_enabled": ("canfd_enabled", "canfdEnabled"),
  "brs_enabled": ("brs_enabled", "brsEnabled"),
  "canfd_non_iso": ("canfd_non_iso", "canfdNonIso"),
  "irq0_call_rate": ("irq0_call_rate", "irq0CallRate"),
  "irq1_call_rate": ("irq1_call_rate", "irq1CallRate"),
  "irq2_call_rate": ("irq2_call_rate", "irq2CallRate"),
  "can_core_reset_count": ("can_core_reset_count", "canCoreResetCount"),
}

def _get_ch_value(ch: Any, key: str) -> Any:
  cands = _CAN_HEALTH_MAP.get(key, (key,))
  return _first_present(ch, cands, None)

def _can_health_to_dict(ch: Any) -> Dict[str, Any]:
  out: Dict[str, Any] = {}
  for k in _CAN_HEALTH_MAP.keys():
    v = _get_ch_value(ch, k)
    if v is None:
      continue
    # normalize capnp enums -> str or int
    if k.startswith("last_"):
      out[k] = str(v)
    else:
      try:
        out[k] = int(v)
      except Exception:
        try:
          out[k] = float(v)
        except Exception:
          out[k] = v
  return out

def _pick_panda_state(ps_msg: Any, serial: str) -> Optional[Any]:
  serial_n = _norm_serial(serial)
  panda_states = _safe_get(ps_msg, "pandaStates", None)
  if panda_states is None:
    return None
  for st in panda_states:
    if _extract_serial(st) == serial_n:
      return st
  return None

def _print_available_serials(ps_msg: Any) -> None:
  panda_states = _safe_get(ps_msg, "pandaStates", None) or []
  serials = [_extract_serial(s) for s in panda_states]
  serials = [s for s in serials if s]
  if serials:
    print(f"[watch] available pandas: {serials}")
  else:
    print("[watch] pandaStates empty. If you killed boardd/pandad, use --usb (may cause LIBUSB busy).")

def _rate(cur: Optional[int], prev: Optional[int], dt: float) -> Optional[float]:
  if cur is None or prev is None or dt <= 0:
    return None
  try:
    return (cur - prev) / dt
  except Exception:
    return None

def watch_messaging(serial: str, bus: int, seconds: float, interval: float, dump_keys: bool) -> int:
  from cereal import messaging  # imported here so script can still run in --usb environments

  sm = messaging.SubMaster(["pandaStates", "controlsState"], poll="pandaStates")
  t0 = time.monotonic()
  last_t = None
  last_ch: Optional[Dict[str, Any]] = None

  while True:
    if seconds and (time.monotonic() - t0) > seconds:
      return 0

    sm.update(timeout=1.0)

    ps = sm["pandaStates"] if sm.updated["pandaStates"] else sm["pandaStates"]
    if ps is None or _safe_get(ps, "pandaStates", None) is None or len(ps.pandaStates) == 0:
      _print_available_serials(ps)
      time.sleep(interval)
      continue

    st = _pick_panda_state(ps, serial)
    if st is None:
      _print_available_serials(ps)
      time.sleep(interval)
      continue

    sm_i, sp_i = _extract_safety(st)
    faults = _first_present(st, ("faults",), 0)
    fault_status = _first_present(st, ("faultStatus", "fault_status"), 0)
    uptime = _first_present(st, ("uptime",), None)
    try:
      faults_i = int(faults)
    except Exception:
      faults_i = 0
    try:
      fs_i = int(fault_status)
    except Exception:
      fs_i = 0
    try:
      up_i = int(uptime) if uptime is not None else None
    except Exception:
      up_i = None

    can_health_list = _safe_get(st, "canHealth", None)
    ch_raw = None
    if can_health_list is not None and bus < len(can_health_list):
      ch_raw = can_health_list[bus]

    if ch_raw is None:
      print(f"[watch] serial={serial} has no canHealth[{bus}] in pandaStates.")
      time.sleep(interval)
      continue

    ch = _can_health_to_dict(ch_raw)

    if dump_keys:
      print("can_health keys:", sorted(ch.keys()))
      print("can_health:", ch)
      return 0

    now = time.monotonic()
    dt = (now - last_t) if last_t is not None else 0.0
    last_t = now

    rx_rate = _rate(ch.get("total_rx_cnt"), (last_ch or {}).get("total_rx_cnt"), dt) if last_ch else None
    tx_rate = _rate(ch.get("total_tx_cnt"), (last_ch or {}).get("total_tx_cnt"), dt) if last_ch else None
    err_rate = _rate(ch.get("total_error_cnt"), (last_ch or {}).get("total_error_cnt"), dt) if last_ch else None
    tx_lost_rate = _rate(ch.get("total_tx_lost_cnt"), (last_ch or {}).get("total_tx_lost_cnt"), dt) if last_ch else None
    last_ch = ch

    def fmt_rate(v: Optional[float], width: int = 6, prec: int = 0) -> str:
      if v is None:
        return " " * (width - 1) + "?"
      if prec == 0:
        return f"{v:{width}.0f}"
      return f"{v:{width}.{prec}f}"

    # Use last_error string fields as our "LEC"-like indicator
    last_err = ch.get("last_error", "?")
    last_stored = ch.get("last_stored_error", "?")

    line = (
      f"t+{(now - t0):6.1f}s "
      f"safety={sm_i if sm_i is not None else '?':>2}/{sp_i if sp_i is not None else '?':<2} "
      f"faults=0x{faults_i:x} fault_status={fs_i} "
      f"CAN{bus}: rx_rate={fmt_rate(rx_rate, 7)} tx_rate={fmt_rate(tx_rate, 5)} "
      f"err_rate={fmt_rate(err_rate, 5)} tx_lost_rate={fmt_rate(tx_lost_rate, 5)} "
      f"bus_off={ch.get('bus_off', '?')} bus_off_cnt={ch.get('bus_off_cnt', '?')} "
      f"rx_err_cnt={ch.get('receive_error_cnt', '?')} tx_err_cnt={ch.get('transmit_error_cnt', '?')} "
      f"last={last_err} stored={last_stored} "
      f"core_resets={ch.get('can_core_reset_count', '?')} "
      + (f"uptime={up_i}" if up_i is not None else "")
    )
    print(line)

    time.sleep(interval)

def watch_usb(serial: str, bus: int, seconds: float, interval: float, dump_keys: bool) -> int:
  # WARNING: This may conflict with boardd/manager which holds the panda USB interface.
  from panda import Panda  # type: ignore

  try:
    p = Panda(serial)
  except Exception as e:
    print(f"[usb] failed to connect to panda {serial}: {e}")
    print("[usb] If you see LIBUSB_ERROR_BUSY, stop manager/boardd or use messaging mode (no --usb).")
    return 2

  t0 = time.monotonic()
  last_t = None
  last_ch: Optional[Dict[str, Any]] = None

  while True:
    if seconds and (time.monotonic() - t0) > seconds:
      return 0

    # Different panda python versions: can_health may or may not exist
    ch_raw = None
    if hasattr(p, "can_health"):
      try:
        ch_raw = p.can_health(bus)
      except Exception:
        ch_raw = None
    if ch_raw is None:
      # Fall back to health() and just print it
      try:
        h = p.health()
      except Exception as e:
        print(f"[usb] health read failed: {e}")
        time.sleep(interval)
        continue
      if dump_keys:
        print("health keys:", sorted(h.keys()))
        print("health:", h)
        return 0
      print(f"t+{(time.monotonic()-t0):6.1f}s (usb) health:", h)
      time.sleep(interval)
      continue

    # Normalize
    ch = dict(ch_raw)
    if dump_keys:
      print("can_health keys:", sorted(ch.keys()))
      print("can_health:", ch)
      return 0

    now = time.monotonic()
    dt = (now - last_t) if last_t is not None else 0.0
    last_t = now

    def g(k: str) -> Optional[int]:
      v = ch.get(k, None)
      if v is None:
        return None
      try:
        return int(v)
      except Exception:
        return None

    rx_rate = _rate(g("total_rx_cnt"), (last_ch or {}).get("total_rx_cnt"), dt) if last_ch else None
    tx_rate = _rate(g("total_tx_cnt"), (last_ch or {}).get("total_tx_cnt"), dt) if last_ch else None
    err_rate = _rate(g("total_error_cnt"), (last_ch or {}).get("total_error_cnt"), dt) if last_ch else None
    tx_lost_rate = _rate(g("total_tx_lost_cnt"), (last_ch or {}).get("total_tx_lost_cnt"), dt) if last_ch else None
    last_ch = {k: g(k) for k in ("total_rx_cnt", "total_tx_cnt", "total_error_cnt", "total_tx_lost_cnt")}

    def fmt_rate(v: Optional[float], width: int = 6) -> str:
      if v is None:
        return " " * (width - 1) + "?"
      return f"{v:{width}.0f}"

    last_err = str(ch.get("last_error", "?"))
    last_stored = str(ch.get("last_stored_error", "?"))

    print(
      f"t+{(now - t0):6.1f}s (usb) "
      f"CAN{bus}: rx_rate={fmt_rate(rx_rate, 7)} tx_rate={fmt_rate(tx_rate, 5)} "
      f"err_rate={fmt_rate(err_rate, 5)} tx_lost_rate={fmt_rate(tx_lost_rate, 5)} "
      f"bus_off={ch.get('bus_off', '?')} bus_off_cnt={ch.get('bus_off_cnt', '?')} "
      f"rx_err_cnt={ch.get('receive_error_cnt', '?')} tx_err_cnt={ch.get('transmit_error_cnt', '?')} "
      f"last={last_err} stored={last_stored} core_resets={ch.get('can_core_reset_count', '?')}"
    )

    time.sleep(interval)

def main() -> int:
  p = argparse.ArgumentParser()
  p.add_argument("--serial", required=True, help="Panda serial (e.g. 2d0054...)")
  p.add_argument("--bus", type=int, default=2, help="CAN bus index to watch (default: 2)")
  p.add_argument("--seconds", type=float, default=0.0, help="Run time (0=forever)")
  p.add_argument("--interval", type=float, default=0.25, help="Print interval seconds")
  p.add_argument("--usb", action="store_true", help="Use direct USB to panda (may cause LIBUSB busy)")
  p.add_argument("--dump-keys", action="store_true", help="Dump available can_health keys and exit")
  args = p.parse_args()

  if args.usb:
    return watch_usb(args.serial, args.bus, args.seconds, args.interval, args.dump_keys)
  return watch_messaging(args.serial, args.bus, args.seconds, args.interval, args.dump_keys)

if __name__ == "__main__":
  raise SystemExit(main())
