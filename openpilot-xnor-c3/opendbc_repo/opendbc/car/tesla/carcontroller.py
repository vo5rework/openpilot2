# /data/openpilot/opendbc/car/tesla/carcontroller.py
"""Tesla CarController (xnor C3)

Stable steering + Unity-parity virtual stalk for cruise speed-limit matching.

What this file does (only two things):
  1) publishes internal 0x659 (fake DAS) on bus 0 and bus 4 for panda safety (existing xnor behavior)
  2) when enabled + Tesla cruise is engaged, nudges Tesla cruise SET speed toward map speed limit
     by emitting STW_ACTN_RQ (cruise stalk up/down), using TeslaCAN.create_action_request() (CRC+counter).

It does *not* change steering behavior or ALC behavior.
"""

from __future__ import annotations

import numpy as np

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.lateral import apply_std_steer_angle_limits

from opendbc.car.tesla.teslacan import TeslaCAN
try:
  from opendbc.car.tesla.teslacan_legacy import TeslaCANLegacy as TeslaCANLegacy
except ImportError:
  from opendbc.car.tesla.teslacan_legacy import TeslaCANRaven as TeslaCANLegacy

from opendbc.car.tesla.values import CarControllerParams, CANBUS, LEGACY_CARS, CAR

try:
  from opendbc.car.tesla.teslacan import create_fake_das_msg as create_fake_das
except ImportError:
  from opendbc.car.tesla.teslacan import create_fake_das_message as create_fake_das


# SpdCtrlLvr_Stat (STW_ACTN_RQ)
BTN_IDLE = 0
BTN_CANCEL = 1
BTN_MAIN = 2
BTN_UP2 = 4
BTN_DOWN2 = 8
BTN_UP1 = 16
BTN_DOWN1 = 32


class CarController(CarControllerBase):
  def __init__(self, dbc_names, CP, VM=None):
    try:
      super().__init__(dbc_names, CP, VM)
    except TypeError:
      super().__init__(dbc_names, CP)

    self.CP = CP
    self.frame = 0

    self.params = Params()
    self._cached_autopilot_disabled = False
    self._cached_pedal_enabled = False
    self._cached_adjust_acc_with_speed_limit = False
    self._cached_speed_limit_offset_uom = 0.0
    self._cached_speed_limit_use_relative = False
    self._params_last_read_frame = -100000

    self._op659_prev_btn = 0
    self.apply_angle_last = 0.0

    self._speed_sync_last_frame = -100000
    self._auto_engage_last_frame = -100000

    self._stw_seed = None
    self._stw_seed_bus = int(CANBUS.party)
    self._stw_last_send_frame = -100000
    self._stw_release_frame = -1
    self._stw_release_bus = int(CANBUS.party)
    self._stw_sequence = []  # list[(frame:int, btn:int)]
    self._op_enabled_prev = False

    if CP.carFingerprint in LEGACY_CARS:
      if CP.carFingerprint in (CAR.TESLA_MODEL_S_HW1, CAR.TESLA_MODEL_X_HW1):
        CANBUS.powertrain = CANBUS.party
        CANBUS.autopilot_powertrain = CANBUS.autopilot_party

      self.packers = {
        CANBUS.party: CANPacker(dbc_names[Bus.party]),
        CANBUS.powertrain: CANPacker(dbc_names[Bus.pt]),
      }
      self.tesla_can = TeslaCANLegacy(self.packers)

      # STW_ACTN_RQ needs CRC/counter; legacy helper doesn't implement it.
      self._action_can_by_bus = {int(bus): TeslaCAN(pkr) for bus, pkr in self.packers.items()}
    else:
      self.packer = CANPacker(dbc_names[Bus.party])
      self.tesla_can = TeslaCAN(self.packer)
      self._action_can_by_bus = {int(CANBUS.party): self.tesla_can}

  def _refresh_cached_params(self) -> None:
    if (self.frame - self._params_last_read_frame) < 50:
      return
    self._params_last_read_frame = int(self.frame)

    self._cached_autopilot_disabled = bool(self.params.get_bool("TinklaAutopilotDisabled"))
    self._cached_pedal_enabled = bool(
      self.params.get_bool("TinklaPedalEnabled") or
      self.params.get_bool("PedalEnabled")
    )
    self._cached_adjust_acc_with_speed_limit = bool(self.params.get_bool("TinklaAdjustAccWithSpeedLimit"))
    self._cached_speed_limit_use_relative = bool(self.params.get_bool("TinklaSpeedLimitUseRelative"))
    try:
      self._cached_speed_limit_offset_uom = float(self.params.get("TinklaSpeedLimitOffset", encoding="utf-8") or "0")
    except Exception:
      self._cached_speed_limit_offset_uom = 0.0

  def _fake_das_buses(self) -> tuple[int, ...]:
    buses = [int(CANBUS.party)]
    try:
      pt = int(CANBUS.powertrain)
      if pt not in buses:
        buses.append(pt)
    except Exception:
      pass
    return tuple(buses)

  def _emit_fake_das_edges(self, can_sends, *, stalk_main: bool = False, stalk_cancel: bool = False) -> None:
    if not stalk_main and not stalk_cancel:
      return
    for bus in self._fake_das_buses():
      can_sends.append(create_fake_das(
        self._cached_pedal_enabled,
        self._cached_autopilot_disabled,
        bus=bus,
        stalk_main=bool(stalk_main),
        stalk_cancel=bool(stalk_cancel),
      ))

  def _emit_internal_0x659(self, CS, can_sends) -> None:
    stalk_btn = int(getattr(CS, "cruise_buttons", 0) or 0)
    prev_btn = int(self._op659_prev_btn)

    main_edge = (stalk_btn == BTN_MAIN) and (prev_btn != BTN_MAIN)
    cancel_edge = (stalk_btn == BTN_CANCEL) and (prev_btn != BTN_CANCEL)

    self._op659_prev_btn = stalk_btn

    if (self.frame % 10 == 0) or main_edge or cancel_edge:
      for bus in self._fake_das_buses():
        can_sends.append(create_fake_das(
          self._cached_pedal_enabled,
          self._cached_autopilot_disabled,
          bus=bus,
          stalk_main=main_edge,
          stalk_cancel=cancel_edge,
        ))

  def _speed_limit_target_ms(self, CS) -> float:
    # Prefer CarState's helper (uses DAS fused if present + supports relative offset)
    try:
      return float(CS._calc_speed_limit_target_ms(str(getattr(CS, "speed_units", "MPH"))))
    except Exception:
      pass

    limit_ms = float(getattr(CS, "speed_limit_ms_das", 0.0) or getattr(CS, "speed_limit_ms", 0.0) or 0.0)
    if limit_ms <= 0.0:
      return 0.0

    off = float(self._cached_speed_limit_offset_uom)
    if self._cached_speed_limit_use_relative:
      return max(0.0, limit_ms * (1.0 + off / 100.0))

    uom = str(getattr(CS, "speed_units", "MPH"))
    return max(0.0, limit_ms + (off * (CV.KPH_TO_MS if uom == "KPH" else CV.MPH_TO_MS)))

  def _stw_bus(self, CS) -> int:
    try:
      return int(getattr(CS, "stw_actn_bus", CANBUS.party))
    except Exception:
      return int(CANBUS.party)

  def _action_can_for_bus(self, bus: int):
    return (
      self._action_can_by_bus.get(int(bus)) or
      self._action_can_by_bus.get(int(CANBUS.party)) or
      next(iter(self._action_can_by_bus.values()))
    )

  def _send_stw(self, CS, can_sends, btn: int, *, bus: int | None = None) -> bool:
    msg = getattr(CS, "msg_stw_actn_req", None)
    if msg is None:
      return False

    b = int(bus if bus is not None else self._stw_bus(CS))

    # Resync seed from the car when idle; preserve our counter across press/release.
    if (self._stw_seed is None) or (int(self._stw_seed_bus) != b) or ((self.frame - int(self._stw_last_send_frame)) > 20):
      self._stw_seed = dict(msg)
      self._stw_seed_bus = int(b)

    mc = int(self._stw_seed.get("MC_STW_ACTN_RQ", 0) or 0)
    used_counter = (mc + 1) % 16

    can_sends.append(self._action_can_for_bus(b).create_action_request(int(b), self._stw_seed, int(btn)))

    if int(btn) == int(BTN_MAIN):
      self._emit_fake_das_edges(can_sends, stalk_main=True)
    elif int(btn) == int(BTN_CANCEL):
      self._emit_fake_das_edges(can_sends, stalk_cancel=True)

    self._stw_seed["MC_STW_ACTN_RQ"] = int(used_counter)
    self._stw_last_send_frame = int(self.frame)
    return True

  def _queue_stalk_pulse(self, CS, can_sends, btn: int) -> bool:
    # Unity-like pulse: press now, release next frame.
    if int(self._stw_release_frame) > int(self.frame):
      return False

    if not self._send_stw(CS, can_sends, btn):
      return False

    self._stw_release_frame = int(self.frame) + 1
    self._stw_release_bus = int(self._stw_seed_bus)
    return True

  def _process_stalk_actions(self, CS, can_sends) -> None:
    # Release pending pulse
    if int(self._stw_release_frame) == int(self.frame):
      self._send_stw(CS, can_sends, BTN_IDLE, bus=int(self._stw_release_bus))
      self._stw_release_frame = -1

    # Run queued press sequence (e.g. legacy MAIN+RESUME on engage)
    if (int(self._stw_release_frame) < 0) and self._stw_sequence:
      due_frame, btn = self._stw_sequence[0]
      if int(self.frame) >= int(due_frame):
        if self._queue_stalk_pulse(CS, can_sends, int(btn)):
          self._stw_sequence.pop(0)

  def _auto_engage_stock_cruise(self, CC, CS) -> None:
    # xnor behavior target: while lateral is active and speed >= 18mph,
    # keep trying to bring stock Tesla cruise up so speed-limit sync can take over.
    if not bool(getattr(CC, "enabled", False) or getattr(CC, "latActive", False)):
      return

    if not self._cached_autopilot_disabled:
      return

    if not self._cached_adjust_acc_with_speed_limit:
      return

    if bool(getattr(CS, "stock_cruise_enabled", False)):
      return

    if float(getattr(CS.out, "vEgo", 0.0) or 0.0) < (18.0 * CV.MPH_TO_MS):
      return

    if (int(self._stw_release_frame) >= 0) or bool(self._stw_sequence):
      return

    # Retry once per second until stock cruise is engaged.
    if (self.frame - int(self._auto_engage_last_frame)) < 100:
      return

    if self.CP.carFingerprint in LEGACY_CARS:
      delay = 10
    else:
      delay = 6

    self._stw_sequence = [(int(self.frame), BTN_MAIN), (int(self.frame) + int(delay), BTN_DOWN1)]
    self._auto_engage_last_frame = int(self.frame)
    cloudlog.info(f"[XNOR_CRUISE_SYNC] auto-engage queued MAIN+SET delay={delay}")

  def _speed_limit_sync(self, CC, CS, can_sends) -> None:
    # Only when OP is engaged (steering control) and user enabled this feature.
    enabled = bool(getattr(CC, "enabled", False) or getattr(CC, "latActive", False))
    if not enabled:
      return

    if not self._cached_autopilot_disabled:
      return

    if not self._cached_adjust_acc_with_speed_limit:
      return

    # Don't overlap with press/release sequencing
    if (int(self._stw_release_frame) >= 0) or bool(self._stw_sequence):
      return

    # Rate limit: 0.5s (Unity parity-ish)
    if (self.frame - int(self._speed_sync_last_frame)) < 50:
      return

    if not bool(getattr(CS, "stock_cruise_enabled", False)):
      if (self.frame % 200) == 0:
        cloudlog.info("[XNOR_CRUISE_SYNC] gated: stock cruise not enabled")
      return

    target_ms = float(self._speed_limit_target_ms(CS))
    current_ms = float(getattr(CS, "stock_cruise_set_speed_ms", 0.0) or 0.0)

    if target_ms <= 0.1 or current_ms <= 0.1:
      if (self.frame % 200) == 0:
        cloudlog.info(
          f"[XNOR_CRUISE_SYNC] gated: target_ms={target_ms:.2f} current_ms={current_ms:.2f} "
          f"speedLimit_ms={float(getattr(CS, 'speed_limit_ms', 0.0) or 0.0):.2f}"
        )
      return

    uom = str(getattr(CS, "speed_units", "MPH"))
    ms_to_u = CV.MS_TO_KPH if uom == "KPH" else CV.MS_TO_MPH
    diff_u = (target_ms - current_ms) * ms_to_u

    # Deadband: ~1 unit
    if abs(diff_u) < 0.9:
      return

    # Choose 5-unit vs 1-unit press
    if diff_u > 0:
      btn = BTN_UP2 if diff_u >= 4.5 else BTN_UP1
    else:
      btn = BTN_DOWN2 if diff_u <= -4.5 else BTN_DOWN1

    if self._queue_stalk_pulse(CS, can_sends, btn):
      self._speed_sync_last_frame = int(self.frame)
      cloudlog.info(
        f"[XNOR_CRUISE_SYNC] uom={uom} target={target_ms*ms_to_u:.1f} current={current_ms*ms_to_u:.1f} "
        f"diff={diff_u:.1f} btn={btn}"
      )
    else:
      if (self.frame % 200) == 0:
        cloudlog.info("[XNOR_CRUISE_SYNC] gated: missing CS.msg_stw_actn_req")

  def update(self, CC, CS, now_nanos):
    actuators = CC.actuators
    can_sends = []

    self._refresh_cached_params()
    self._emit_internal_0x659(CS, can_sends)

    autopilot_disabled = bool(self._cached_autopilot_disabled)

    # Always define before use
    human_control = bool(getattr(CS, "human_control", False))

    op_enabled = bool(getattr(CC, "enabled", False) or getattr(CC, "latActive", False))
    self._op_enabled_prev = bool(op_enabled)

    self._auto_engage_stock_cruise(CC, CS)

    self._process_stalk_actions(CS, can_sends)

    self._speed_limit_sync(CC, CS, can_sends)

    lat_active = (
      bool(CC.latActive) and
      autopilot_disabled and
      (not CS.out.cruiseState.standstill) and
      (not human_control)
    )

    # Steering (50Hz)
    if self.frame % 2 == 0:
      if human_control:
        self.apply_angle_last = float(CS.out.steeringAngleDeg)
      else:
        self.apply_angle_last = float(apply_std_steer_angle_limits(
          float(actuators.steeringAngleDeg),
          float(self.apply_angle_last),
          float(getattr(CS.out, "vEgoRaw", CS.out.vEgo)),
          float(CS.out.steeringAngleDeg),
          lat_active,
          CarControllerParams.ANGLE_LIMITS,
        ))

      if self.CP.carFingerprint in LEGACY_CARS:
        counter = (self.frame // 2) % 16
        can_sends.append(
          self.tesla_can.create_steering_control(counter, self.apply_angle_last, lat_active)
        )
      else:
        can_sends.append(
          self.tesla_can.create_steering_control(self.apply_angle_last, lat_active)
        )

    # EPS allow (legacy)
    if (self.CP.carFingerprint in LEGACY_CARS) and (self.frame % 10 == 0):
      counter = (self.frame // 10) % 16
      can_sends.append(self.tesla_can.create_steering_allowed(counter))

    # Longitudinal (optional)
    if self.CP.openpilotLongitudinalControl and (self.frame % 4 == 0):
      state = 13 if CC.cruiseControl.cancel else 4
      accel = float(np.clip(
        float(actuators.accel),
        CarControllerParams.ACCEL_MIN,
        CarControllerParams.ACCEL_MAX
      ))
      counter = (self.frame // 4) % 8
      long_active = bool(CC.longActive) and (not autopilot_disabled)

      can_sends.append(
        self.tesla_can.create_longitudinal_command(
          state,
          accel,
          counter,
          float(CS.out.vEgo),
          long_active
        )
      )

    new_actuators = actuators.as_builder()
    new_actuators.steeringAngleDeg = float(self.apply_angle_last)

    self.frame += 1
    return new_actuators, can_sends
