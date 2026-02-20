"""/data/openpilot/selfdrive/car/modules/BLNK_module.py

Unity-style "tap blinker" detection.

- Tap/comfort indicator triggers ALC.
- Tap direction is held until the indicator lamp turns off (comfort blink finished).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _TapState:
  tap_direction: int = 0
  blinker_on_frame_start: int = 0
  prev_turnSignalStalkState: int = 0


class BLNKController:
  def __init__(self, tap_detect_frames: int = 55) -> None:
    self.tap_detect_frames = int(tap_detect_frames)
    self._s = _TapState()

  @property
  def tap_direction(self) -> int:
    return int(self._s.tap_direction)

  @tap_direction.setter
  def tap_direction(self, v: int) -> None:
    self._s.tap_direction = int(v)

  def _reset(self) -> None:
    self._s.tap_direction = 0
    self._s.blinker_on_frame_start = 0

  def _lamp_active(self, CS) -> bool:
    if self._s.tap_direction == 1:
      return bool(getattr(CS, "leftBlinkerLamp", False))
    if self._s.tap_direction == 2:
      return bool(getattr(CS, "rightBlinkerLamp", False))
    return False

  def update_state(self, CS, frame: int) -> None:
    stalk = int(getattr(CS, "turnSignalStalkState", 0) or 0)

    # opposite direction => cancel
    if self._s.tap_direction and stalk and self._s.tap_direction != stalk:
      self._reset()

    # rising edge
    if stalk and self._s.prev_turnSignalStalkState == 0:
      self._s.blinker_on_frame_start = int(frame)

    # falling edge => tap?
    elif stalk == 0 and self._s.prev_turnSignalStalkState:
      dur = int(frame) - int(self._s.blinker_on_frame_start)
      if 0 <= dur <= self.tap_detect_frames:
        self._s.tap_direction = int(self._s.prev_turnSignalStalkState)
      else:
        self._reset()

    # keep tap until lamp ends
    if self._s.tap_direction and stalk == 0 and (not self._lamp_active(CS)):
      self._reset()

    self._s.prev_turnSignalStalkState = stalk
