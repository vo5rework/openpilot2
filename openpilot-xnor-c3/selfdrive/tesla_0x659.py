#!/usr/bin/env python3
"""
Tesla internal 0x659 "carrier" message generator.

Purpose
-------
Some Tesla forks use a synthetic CAN frame (0x659) as an internal "contract"
between userspace and panda safety:
  - Byte 5 bit 0x40: controls allowed (enable latch)
  - Byte 5 bit 0x80: autopilot disabled (platform-specific flag)
  - Byte 5 bit 0x20: pedal enabled (optional; may be unused in your build)

This module is intentionally **not** a publisher. It only returns CanData frames
to be sent by an existing sendcan publisher (e.g. card.py), avoiding
MultiplePublishersError.

Integration
-----------
In your CAN loop (e.g. card.py):
  carrier = Tesla659Carrier()
  extra = carrier.tick(can_list)
  if extra: sendcan.send(can_list_to_can_capnp(extra, ...))

Safety note
-----------
This is safety-critical code. Use only in controlled testing. Ensure panda safety
and vehicle interfaces are configured correctly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Sequence

from opendbc.car.can_definitions import CanData

try:
  # Optional; we tolerate missing params keys (older builds)
  from openpilot.common.params import Params  # type: ignore
except Exception:  # pragma: no cover
  Params = None  # type: ignore


ADDR_STALK = 0x45
ADDR_CARRIER = 0x659

# Known params keys across forks/casing variants
AP_DISABLED_KEYS = (
  "TinklaAutopilotDisabled",
  "Tinklaautopilotdisabled",
  "AutopilotDisabled",
)
PEDAL_ENABLED_KEYS = (
  "TinklaPedalEnabled",
  "TinklaPedal",
  "PedalEnabled",
)


def _lever_position(dat: bytes) -> int:
  # Matches the parsing you've been using in your watcher scripts
  return (dat[0] & 0x3F) if dat else -1


def _safe_get_bool(params_obj, key: str) -> bool | None:
  """
  Read Params.get_bool if available; returns None if key doesn't exist in params schema.
  """
  if params_obj is None:
    return None
  try:
    return bool(params_obj.get_bool(key))
  except Exception:
    # UnknownKeyName or other params errors
    return None


@dataclass
class _Edges:
  main_edge: bool = False
  cancel_edge: bool = False


class Tesla659Carrier:
  """
  Builds synthetic 0x659 CAN frames for multiple buses.

  Default buses (0, 4) match your observed sendcan buses for dual-panda setups.
  Adjust if your topology differs.
  """

  def __init__(
    self,
    buses: Sequence[int] = (0, 4),
    send_hz: float = 50.0,
  ) -> None:
    self._buses = tuple(int(b) for b in buses)
    self._send_period_s = 1.0 / float(send_hz) if send_hz > 0 else 0.0

    self._last_send_ts = 0.0
    self._last_lever = 0
    self._lever = 0
    self._edges = _Edges()

    self._params = Params() if Params is not None else None
    self._params_cache_ts = 0.0
    self._ap_disabled = True   # conservative default
    self._pedal_enabled = False

  @property
  def lever(self) -> int:
    return self._lever

  @property
  def edges(self) -> _Edges:
    return self._edges

  def update_from_can(self, can_list: Iterable[CanData]) -> None:
    """
    Parse stalk (0x45) from raw CAN to drive enable-latch.
    """
    main_edge = False
    cancel_edge = False

    for m in can_list:
      if m.address != ADDR_STALK:
        continue
      dat = bytes(m.dat)
      lever = _lever_position(dat)

      # MAIN: lever==2 (your logs show 2 when pulled)
      if lever == 2 and self._last_lever != 2:
        main_edge = True

      # CANCEL: transition away from 2 (including to 0)
      if lever != 2 and self._last_lever == 2:
        cancel_edge = True

      self._lever = lever
      self._last_lever = lever

    self._edges = _Edges(main_edge=main_edge, cancel_edge=cancel_edge)

  def _refresh_params(self) -> None:
    """
    Throttle params reads to ~2 Hz to reduce overhead.
    """
    now = time.monotonic()
    if (now - self._params_cache_ts) < 0.5:
      return
    self._params_cache_ts = now

    # AutopilotDisabled flag (optional)
    for k in AP_DISABLED_KEYS:
      v = _safe_get_bool(self._params, k)
      if v is not None:
        self._ap_disabled = v
        break

    # Pedal enabled flag (optional; can be ignored by leaving default False)
    for k in PEDAL_ENABLED_KEYS:
      v = _safe_get_bool(self._params, k)
      if v is not None:
        self._pedal_enabled = v
        break

  def _build_b5(self) -> int:
    """
    Construct carrier byte5.
    - 0x40 enables controls (this is what you were missing)
    - 0x80 denotes AP disabled (Unity parity)
    - 0x20 denotes pedal enabled (optional)
    """
    self._refresh_params()

    controls_allow = (self._lever == 2)  # direct contract: stalk pulled => enable

    b5 = 0
    if controls_allow:
      b5 |= 0x40
    if self._ap_disabled:
      b5 |= 0x80
    if self._pedal_enabled:
      b5 |= 0x20
    return b5

  def build_msgs(self) -> list[CanData]:
    """
    Build 0x659 frames for all configured buses.
    """
    b5 = self._build_b5()
    dat = bytes([0, 0, 0, 0, 0, b5, 0, 0])
    return [CanData(ADDR_CARRIER, dat, bus) for bus in self._buses]

  def tick(self, can_list: Sequence[CanData]) -> list[CanData]:
    """
    Update internal state from CAN and (rate-limited) return frames to send.
    """
    self.update_from_can(can_list)

    if self._send_period_s <= 0:
      return self.build_msgs()

    now = time.monotonic()
    if (now - self._last_send_ts) >= self._send_period_s:
      self._last_send_ts = now
      return self.build_msgs()
    return []
