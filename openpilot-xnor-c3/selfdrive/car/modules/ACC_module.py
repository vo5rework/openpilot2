"""/data/openpilot/selfdrive/car/modules/ACC_module.py

ACC stage 1 (Unity-aligned intent, xnor-friendly implementation).

Adjust Tesla stock cruise set speed toward the speed limit target (speed limit + offset)
using STW_ACTN_RQ stalk emulation.

This module only outputs which stalk button to emulate + whether to send a frame.
"""

from __future__ import annotations

from typing import Tuple

from opendbc.car.tesla.values import CruiseButtons
from opendbc.car.common.conversions import Conversions as CV


class ACCController:
  def __init__(self, press_cooldown_frames: int = 50, human_guard_frames: int = 300, auto_guard_frames: int = 40) -> None:
    self._cooldown_default = int(press_cooldown_frames)
    self._cooldown_frames = 0
    self._last_human_action_frame = -100000
    self._last_auto_action_frame = -100000
    self._human_guard_frames = int(human_guard_frames)
    self._auto_guard_frames = int(auto_guard_frames)

  @staticmethod
  def _unit_steps_kph(speed_units: str) -> Tuple[float, float]:
    if speed_units == "MPH":
      return 1.0 * CV.MPH_TO_KPH, 5.0 * CV.MPH_TO_KPH
    return 1.0, 5.0

  def note_human_action(self, frame: int, cruise_button: int) -> None:
    if int(cruise_button) != int(CruiseButtons.IDLE):
      self._last_human_action_frame = int(frame)

  def note_automated_action(self, frame: int) -> None:
    self._last_auto_action_frame = int(frame)
    self._cooldown_frames = self._cooldown_default

  def update(self, CS, *, lat_active: bool, frame: int) -> Tuple[bool, int]:
    """Return (should_send, cruise_button)."""

    if self._cooldown_frames > 0:
      self._cooldown_frames -= 1
      return False, int(CruiseButtons.IDLE)

    if not getattr(CS, "enableACC", False):
      return False, int(CruiseButtons.IDLE)

    if not lat_active:
      return False, int(CruiseButtons.IDLE)

    # avoid fighting active stalk presses
    if int(getattr(CS, "cruise_buttons", 0) or 0) != int(CruiseButtons.IDLE):
      return False, int(CruiseButtons.IDLE)

    if (int(frame) - int(self._last_human_action_frame)) < self._human_guard_frames:
      return False, int(CruiseButtons.IDLE)

    if (int(frame) - int(self._last_auto_action_frame)) < self._auto_guard_frames:
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

    current_ms = float(getattr(CS, "stock_cruise_set_speed_ms", cs_out.cruiseState.speed) or 0.0)
    if current_ms <= 0.0:
      return False, int(CruiseButtons.IDLE)

    diff_kph = (target_ms - current_ms) * CV.MS_TO_KPH
    half_step, full_step = self._unit_steps_kph(getattr(CS, "speed_units", "KPH"))

    if diff_kph > 0.0:
      if diff_kph < 0.9 * half_step:
        return False, int(CruiseButtons.IDLE)
    else:
      if abs(diff_kph) < 0.9 * half_step:
        return False, int(CruiseButtons.IDLE)

    if diff_kph <= (-2.0 * full_step):
      return True, int(CruiseButtons.CANCEL)

    if diff_kph <= (-0.6 * full_step):
      return True, int(CruiseButtons.DECEL_2ND)

    if diff_kph < (-0.9 * half_step):
      return True, int(CruiseButtons.DECEL_SET)

    if diff_kph >= full_step:
      return True, int(CruiseButtons.RES_ACCEL_2ND)

    if diff_kph >= half_step:
      return True, int(CruiseButtons.RES_ACCEL)

    return False, int(CruiseButtons.IDLE)
