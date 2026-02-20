"""/data/openpilot/selfdrive/car/modules/ACC_module.py

Unity-aligned ACC stalk decision policy for xnor.

This module intentionally owns *policy* only:
- whether a stalk command should be sent
- which cruise button to emulate

xnor's Tesla CarController remains responsible for:
- STW_ACTN_RQ transport (CRC/counter)
- press/release pulse sequencing
- bus routing details
"""

from __future__ import annotations

from typing import Tuple

from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.tesla.values import CruiseButtons


class ACCController:
  # Mirrors Unity's practical floor for stock cruise operation.
  MIN_CRUISE_SPEED_MS = 17.1 * CV.MPH_TO_MS

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

  def note_human_action(self, frame: int, cruise_button: int, prev_button: int) -> None:
    # Match Unity intent: track user action when stalk command changes away from IDLE.
    cur = int(cruise_button)
    prev = int(prev_button)
    if cur != prev and cur != int(CruiseButtons.IDLE):
      self._last_human_action_frame = int(frame)

  def note_automated_action(self, frame: int) -> None:
    self._last_auto_action_frame = int(frame)
    self._cooldown_frames = self._cooldown_default

  def _guarded(self, frame: int) -> bool:
    if self._cooldown_frames > 0:
      self._cooldown_frames -= 1
      return True

    if (int(frame) - int(self._last_human_action_frame)) < self._human_guard_frames:
      return True

    if (int(frame) - int(self._last_auto_action_frame)) < self._auto_guard_frames:
      return True

    return False

  def update(self, CS, *, lat_active: bool, frame: int) -> Tuple[bool, int]:
    """Return (should_send, cruise_button)."""
    if self._guarded(frame):
      return False, int(CruiseButtons.IDLE)

    if not bool(getattr(CS, "enableACC", False)):
      return False, int(CruiseButtons.IDLE)

    if not lat_active:
      return False, int(CruiseButtons.IDLE)

    if int(getattr(CS, "cruise_buttons", 0) or 0) != int(CruiseButtons.IDLE):
      return False, int(CruiseButtons.IDLE)

    cs_out = getattr(CS, "out", None)
    if cs_out is None or getattr(cs_out, "cruiseState", None) is None:
      return False, int(CruiseButtons.IDLE)

    if not bool(cs_out.cruiseState.enabled):
      return False, int(CruiseButtons.IDLE)

    if bool(getattr(cs_out.cruiseState, "standstill", False)):
      return False, int(CruiseButtons.IDLE)

    if not bool(getattr(CS, "_tinkla", None) and getattr(CS._tinkla, "adjust_acc_with_speed_limit", False)):
      return False, int(CruiseButtons.IDLE)

    desired_speed_ms = float(CS._calc_speed_limit_target_ms(getattr(CS, "speed_units", "KPH")))
    current_speed_ms = float(getattr(CS, "stock_cruise_set_speed_ms", cs_out.cruiseState.speed) or 0.0)

    if desired_speed_ms <= 0.0 or current_speed_ms <= 0.0:
      return False, int(CruiseButtons.IDLE)

    # Keep sync passive below stock cruise floor to avoid cancel-trigger side effects.
    if desired_speed_ms < self.MIN_CRUISE_SPEED_MS:
      return False, int(CruiseButtons.IDLE)

    speed_offset_kph = (desired_speed_ms - current_speed_ms) * CV.MS_TO_KPH
    half_step_kph, full_step_kph = self._unit_steps_kph(getattr(CS, "speed_units", "KPH"))

    if speed_offset_kph < -0.6 * full_step_kph and current_speed_ms > 0.0:
      return True, int(CruiseButtons.DECEL_2ND)
    if speed_offset_kph < -0.9 * half_step_kph and current_speed_ms > 0.0:
      return True, int(CruiseButtons.DECEL_SET)

    if float(getattr(cs_out, "vEgo", 0.0) or 0.0) <= self.MIN_CRUISE_SPEED_MS:
      return False, int(CruiseButtons.IDLE)

    if speed_offset_kph >= full_step_kph:
      return True, int(CruiseButtons.RES_ACCEL_2ND)
    if speed_offset_kph >= half_step_kph:
      return True, int(CruiseButtons.RES_ACCEL)

    return False, int(CruiseButtons.IDLE)
