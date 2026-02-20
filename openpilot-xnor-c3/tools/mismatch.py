#!/usr/bin/env python3
"""
/data/openpilot/tools/why_controls_mismatch.py

Prints the live conditions that trigger 'controlsMismatch' on XNOR-style openpilot builds:
  - safety config mismatch (pandaStates vs CarParams)
  - pandaState.safetyRxChecksInvalid
  - enabled but any panda controlsAllowed == false for long enough

Run with openpilot running (do NOT pkill boardd/pandad):
  python3 tools/why_controls_mismatch.py
"""
import time
from collections import Counter
from contextlib import nullcontext

from cereal import car

try:
  import cereal.messaging as messaging
except Exception:
  from cereal import messaging  # type: ignore

try:
  from openpilot.common.params import Params
except Exception:
  from common.params import Params  # type: ignore


def enum_to_int(x) -> int:
  if x is None:
    return 0
  if isinstance(x, int):
    return x
  raw = getattr(x, "raw", None)
  if raw is not None:
    return int(raw)
  val = getattr(x, "value", None)
  if val is not None:
    return int(val)
  try:
    return int(x)
  except Exception:
    # last resort: try mapping by name (e.g., "silent")
    name = str(x)
    sm = getattr(car.CarParams, "SafetyModel", None)
    if sm is not None and hasattr(sm, name):
      return int(getattr(sm, name).raw)
    raise


def as_int_or_list(x):
  """
  pandaState.faults is sometimes:
    - an int bitmask (older)
    - a DynamicListReader of enums (newer)
  """
  if x is None:
    return 0
  if isinstance(x, int):
    return x
  # capnp list reader
  try:
    return [str(v) for v in x]
  except Exception:
    return str(x)


def as_ctx(obj_or_ctx):
  if hasattr(obj_or_ctx, "__enter__") and hasattr(obj_or_ctx, "__exit__"):
    return obj_or_ctx
  return nullcontext(obj_or_ctx)


def iter_safety_cfgs(cp):
  if hasattr(cp, "safetyConfigs") and cp.safetyConfigs is not None:
    return list(cp.safetyConfigs)
  if hasattr(cp, "safetyConfig") and cp.safetyConfig is not None:
    return [cp.safetyConfig]
  if hasattr(cp, "safetyModel") and hasattr(cp, "safetyParam"):
    class _Cfg:
      safetyModel = cp.safetyModel
      safetyParam = cp.safetyParam
    return [_Cfg()]
  raise RuntimeError("CarParams schema missing safety config fields")


def cfg_tuple(obj):
  return (enum_to_int(getattr(obj, "safetyModel", 0)), int(getattr(obj, "safetyParam", 0)))


def panda_states_list(ps_msg):
  if isinstance(ps_msg, (list, tuple)):
    return list(ps_msg)
  try:
    return list(ps_msg)
  except Exception:
    return [ps_msg]


def multiset_equal(a, b) -> bool:
  return Counter(a) == Counter(b)


def main():
  raw = Params().get("CarParams", block=True)
  if raw is None:
    raise SystemExit("CarParams missing from Params")

  cp_ctx = car.CarParams.from_bytes(raw)
  with as_ctx(cp_ctx) as cp:
    expected = [cfg_tuple(c) for c in iter_safety_cfgs(cp)]

  ignored_modes = {
    enum_to_int(getattr(car.CarParams.SafetyModel, "silent", 0)),
    enum_to_int(getattr(car.CarParams.SafetyModel, "noOutput", 1)),
  }

  print("\nEXPECTED safety configs (from CarParams):")
  for i, (m, p) in enumerate(expected):
    print(f"  cfg[{i}]: safetyModel={m} safetyParam={p}")

  sm = messaging.SubMaster(["pandaStates", "controlsState"])

  mismatch_counter = 0
  print("\nWatching live pandaStates + controlsState...\n")

  while True:
    sm.update(100)

    enabled = bool(getattr(sm["controlsState"], "enabled", False))
    panda_states = panda_states_list(sm["pandaStates"])

    # Wait until we have at least the expected number of pandaStates before judging mismatch.
    have_all = len(panda_states) >= len(expected) and len(expected) > 0

    actual = [cfg_tuple(ps) for ps in panda_states[:len(expected)]] if have_all else []
    safety_mismatch = (have_all and (not multiset_equal(actual, expected)))

    rx_checks_invalid = any(bool(getattr(ps, "safetyRxChecksInvalid", False)) for ps in panda_states)

    any_not_allowed = enabled and any(
      (enum_to_int(getattr(ps, "safetyModel", -1)) not in ignored_modes)
      and (not bool(getattr(ps, "controlsAllowed", True)))
      for ps in panda_states
    )
    mismatch_counter = mismatch_counter + 1 if any_not_allowed else 0

    print("-" * 120)
    print(f"enabled={enabled} mismatch_counter={mismatch_counter} have_all_pandas={have_all}")
    print(f"safety_mismatch={safety_mismatch} rx_checks_invalid={rx_checks_invalid}")

    if have_all:
      print(f"expected={expected}")
      print(f"actual  ={actual}")

    for i, ps in enumerate(panda_states):
      smode = enum_to_int(getattr(ps, "safetyModel", -1))
      sparm = int(getattr(ps, "safetyParam", 0))
      allowed = bool(getattr(ps, "controlsAllowed", False))
      rxinv = bool(getattr(ps, "safetyRxChecksInvalid", False))
      faults = as_int_or_list(getattr(ps, "faults", None))
      if isinstance(faults, int):
        faults_str = f"0x{faults:x}"
      else:
        faults_str = ",".join(faults) if isinstance(faults, list) else str(faults)
      print(f"panda[{i}]: safetyModel={smode} safetyParam={sparm} controlsAllowed={allowed} "
            f"rxChecksInvalid={rxinv} faults={faults_str}")

    time.sleep(0.25)


if __name__ == "__main__":
  main()
