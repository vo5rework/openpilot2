"""/data/openpilot/selfdrive/car/modules/ACC_module.py

ACC stage 1 (Unity-aligned intent, xnor-friendly implementation).

Adjust Tesla stock cruise set speed toward the speed limit target (speed limit + offset)
using STW_ACTN_RQ stalk emulation.

This module only outputs which stalk button to emulate + whether to send a frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from opendbc.car.tesla.values import CruiseButtons
from opendbc.car.common.conversions import Conversions as CV


@dataclass
class _AccState:
  cooldown_frames: int = 0
  need_idle_frame: bool = False


class ACCController:
  def __init__(self, press_cooldown_frames: int = 20) -> None:
    self._cooldown_default = int(press_cooldown_frames)
    self._s = _AccState()

  @staticmethod
  def _full_step_kph(speed_units: str) -> float:
    return 5.0 * CV.MPH_TO_KPH if speed_units == "MPH" else 5.0

  def update(self, CS, *, lat_active: bool) -> Tuple[bool, int]:
    """Return (should_send, cruise_button)."""

    if self._s.need_idle_frame:
      self._s.need_idle_frame = False
      return True, int(CruiseButtons.IDLE)

    if self._s.cooldown_frames > 0:
      self._s.cooldown_frames -= 1
      return False, int(CruiseButtons.IDLE)

    if not getattr(CS, "enableACC", False):
      return False, int(CruiseButtons.IDLE)

    if not lat_active:
      return False, int(CruiseButtons.IDLE)

    # avoid fighting the driver
    if int(getattr(CS, "cruise_buttons", 0) or 0) != int(CruiseButtons.IDLE):
      return False, int(CruiseButtons.IDLE)

    cs_out = getattr(CS, "out", None)
    if cs_out is None or getattr(cs_out, "cruiseState", None) is None:
      return False, int(CruiseButtons.IDLE)

    if not bool(cs_out.cruiseState.enabled):
      return False, int(CruiseButtons.IDLE)

    if not bool(getattr(CS, "_tinkla", None) and getattr(CS._tinkla, "adjust_acc_with_speed_limit", False)):
      return False, int(CruiseButtons.IDLE)

    target_ms = float(CS._calc_speed_limit_target_ms(getattr(CS, "speed_units", "KPH")))
    if target_ms <= 0.0:
      return False, int(CruiseButtons.IDLE)

    current_ms = float(cs_out.cruiseState.speed)
    if current_ms <= 0.0:
      return False, int(CruiseButtons.IDLE)

    diff_kph = (target_ms - current_ms) / CV.KPH_TO_MS
    if abs(diff_kph) < 0.6:
      return False, int(CruiseButtons.IDLE)

    full_step = self._full_step_kph(getattr(CS, "speed_units", "KPH"))

    if diff_kph > 0:
      btn = CruiseButtons.RES_ACCEL_2ND if abs(diff_kph) >= (full_step - 0.5) else CruiseButtons.RES_ACCEL
    else:
      btn = CruiseButtons.DECEL_2ND if abs(diff_kph) >= (full_step - 0.5) else CruiseButtons.DECEL_SET

    self._s.cooldown_frames = self._cooldown_default
    self._s.need_idle_frame = True
    return True, int(btn)
