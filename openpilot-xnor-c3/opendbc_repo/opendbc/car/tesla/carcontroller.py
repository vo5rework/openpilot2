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
from openpilot.selfdrive.car.modules.ACC_module import ACCController
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
    self._params_last_read_frame = -100000

    self._op659_prev_btn = 0
    self.apply_angle_last = 0.0

    self.acc_controller = ACCController()
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

  def _emit_internal_0x659(self, CS, can_sends) -> None:
    stalk_btn = int(getattr(CS, "cruise_buttons", 0) or 0)
    prev_btn = int(self._op659_prev_btn)

    main_edge = (stalk_btn == BTN_MAIN) and (prev_btn != BTN_MAIN)
    cancel_edge = (stalk_btn == BTN_CANCEL) and (prev_btn != BTN_CANCEL)

    self._op659_prev_btn = stalk_btn
    self.acc_controller.note_human_action(self.frame, stalk_btn, prev_btn)

    if (self.frame % 10 == 0) or main_edge or cancel_edge:
      for bus in (CANBUS.party,):
        can_sends.append(create_fake_das(
          self._cached_pedal_enabled,
          self._cached_autopilot_disabled,
          bus=bus,
          stalk_main=main_edge,
          stalk_cancel=cancel_edge,
        ))

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

  def _queue_auto_cruise_engage(self, CS, can_sends, *, autopilot_disabled: bool, op_enabled: bool) -> None:
    v_ego = float(getattr(CS.out, "vEgo", 0.0) or 0.0)
    above_threshold = v_ego >= (18.0 * CV.MPH_TO_MS)

    if not (autopilot_disabled and op_enabled and above_threshold):
      return

    if not self._cached_adjust_acc_with_speed_limit:
      return

    if bool(getattr(CS, "stock_cruise_enabled", False)):
      return

    # avoid overlapping with an active/queued pulse
    if (int(self._stw_release_frame) >= 0) or bool(self._stw_sequence):
      return

    # retry at a conservative cadence while cruise is not yet engaged
    if (self.frame - int(self._auto_engage_last_frame)) < 100:
      return

    if self.CP.carFingerprint in LEGACY_CARS:
      seq = [(int(self.frame), BTN_MAIN), (int(self.frame) + 10, BTN_DOWN1)]
    else:
      # Non-legacy: MAIN + short delayed SET matches Unity intent for engage-from-lateral.
      seq = [(int(self.frame), BTN_MAIN), (int(self.frame) + 6, BTN_DOWN1)]

    self._stw_sequence = seq
    self._auto_engage_last_frame = int(self.frame)
    cloudlog.info(f"[XNOR_CRUISE_SYNC] auto-engage queued vEgo={v_ego*CV.MS_TO_MPH:.1f}mph legacy={self.CP.carFingerprint in LEGACY_CARS}")

  def _speed_limit_sync(self, CC, CS, can_sends) -> None:
    # Only when OP is engaged (steering control); ACC module handles feature/user gates.
    lat_active = bool(getattr(CC, "enabled", False) or getattr(CC, "latActive", False))
    if not lat_active:
      return

    if not self._cached_autopilot_disabled:
      return

    # Don't overlap with press/release sequencing
    if (int(self._stw_release_frame) >= 0) or bool(self._stw_sequence):
      return

    if not bool(getattr(CS, "stock_cruise_enabled", False)):
      return

    # Keep a conservative cadence to minimize command churn.
    if (self.frame - int(self._speed_sync_last_frame)) < 50:
      return

    should_send, btn = self.acc_controller.update(CS, lat_active=lat_active, frame=int(self.frame))
    if not should_send:
      return

    if self._queue_stalk_pulse(CS, can_sends, int(btn)):
      self._speed_sync_last_frame = int(self.frame)
      self.acc_controller.note_automated_action(int(self.frame))
    elif (self.frame % 200) == 0:
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

    self._queue_auto_cruise_engage(CS, can_sends, autopilot_disabled=autopilot_disabled, op_enabled=op_enabled)

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
