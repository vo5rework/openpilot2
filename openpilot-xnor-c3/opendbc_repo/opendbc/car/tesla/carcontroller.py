# /data/openpilot/opendbc/car/tesla/carcontroller.py
"""Tesla CarController (xnor C3)

Stable steering + Unity-parity virtual stalk for cruise speed-limit matching.

Key HW2/Legacy behavior (matches Unity + your stalk2 capture):
  - STW_ACTN_RQ is sent only on 10Hz ticks (frame % 10 == 0)
  - button presses are held 2 ticks (cancel 3 ticks), then released to IDLE
  - payload is mirrored on BOTH bus 0 (party) and bus 2 (autopilot_party) with identical bytes
  - counter/CRC comes from TeslaCAN.create_action_request() and is kept consistent across both buses
"""

from __future__ import annotations

from dataclasses import dataclass

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
BTN_MAIN = 2   # HW2: RWD pull (engage)
BTN_UP2 = 4
BTN_DOWN2 = 8
BTN_UP1 = 16
BTN_DOWN1 = 32


@dataclass
class _StalkAction:
  btn: int
  hold_ticks: int


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

    # Virtual stalk sender state (Unity parity)
    self._stw_seed: dict | None = None
    self._stw_seed_bus_from_car = int(CANBUS.party)
    self._stw_last_seed_frame = -100000

    self._stw_active_btn = BTN_IDLE
    self._stw_active_ticks_left = 0
    self._stw_release_ticks_left = 0
    self._stw_queue: list[_StalkAction] = []

    # 10Hz STW scheduler (Unity parity): use time, not frame, to avoid drift
    self._stw_tick_ns = 100_000_000  # 100ms
    self._stw_next_tick_ns: int | None = None

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

  def _emit_internal_0x659(self, CS, can_sends) -> None:
    stalk_btn = int(getattr(CS, "cruise_buttons", 0) or 0)
    prev_btn = int(self._op659_prev_btn)

    main_edge = (stalk_btn == BTN_MAIN) and (prev_btn != BTN_MAIN)
    cancel_edge = (stalk_btn == BTN_CANCEL) and (prev_btn != BTN_CANCEL)

    self._op659_prev_btn = stalk_btn

    if (self.frame % 10 == 0) or main_edge or cancel_edge:
      buses = {int(CANBUS.party)}
      if self.CP.carFingerprint in LEGACY_CARS:
        buses.add(int(CANBUS.powertrain))
      for bus in sorted(buses):
        can_sends.append(create_fake_das(
          self._cached_pedal_enabled,
          self._cached_autopilot_disabled,
          bus=bus,
          stalk_main=main_edge,
          stalk_cancel=cancel_edge,
        ))

  def _speed_limit_target_ms(self, CS) -> float:
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

  def _action_can_for_bus(self, bus: int) -> TeslaCAN:
    return (
      self._action_can_by_bus.get(int(bus)) or
      self._action_can_by_bus.get(int(CANBUS.party)) or
      next(iter(self._action_can_by_bus.values()))
    )

  def _stw_mirror_buses(self) -> tuple[int, ...]:
    b0 = int(getattr(CANBUS, "party", 0))
    b2 = int(getattr(CANBUS, "autopilot_party", b0))
    return (b0,) if b2 == b0 else (b0, b2)

  def _stw_in_progress(self) -> bool:
    return bool(self._stw_queue) or (self._stw_active_ticks_left > 0) or (self._stw_release_ticks_left > 0)

  def _stw_resync_seed_from_car(self, CS) -> None:
    msg = getattr(CS, "msg_stw_actn_req", None)
    if not isinstance(msg, dict):
      return
    if int(msg.get("SpdCtrlLvr_Stat", BTN_IDLE) or 0) != BTN_IDLE:
      return
    self._stw_seed = dict(msg)
    self._stw_seed_bus_from_car = int(getattr(CS, "stw_actn_bus", CANBUS.party))
    self._stw_last_seed_frame = int(self.frame)

  def _stw_send_mirrored(self, CS, can_sends, btn: int) -> bool:
    msg = getattr(CS, "msg_stw_actn_req", None)
    if (self._stw_seed is None) and isinstance(msg, dict):
      self._stw_seed = dict(msg)
      self._stw_seed_bus_from_car = int(getattr(CS, "stw_actn_bus", CANBUS.party))
      self._stw_last_seed_frame = int(self.frame)

    if self._stw_seed is None:
      return False

    mirror = self._stw_mirror_buses()
    primary_bus = int(mirror[0])

    mc = int(self._stw_seed.get("MC_STW_ACTN_RQ", 0) or 0)
    next_ctr = (mc + 1) % 16

    try:
      addr, _, dat = self._action_can_for_bus(primary_bus).create_action_request(primary_bus, self._stw_seed, int(btn))
    except Exception:
      return False

    # Mirror identical payload bytes across both buses (Unity parity)
    for bus in mirror:
      can_sends.append((addr, int(bus), dat))

    # Keep seed counter aligned with the message we just sent (create_action_request increments once).
    self._stw_seed["MC_STW_ACTN_RQ"] = int(next_ctr)
    return True
  def _stw_enqueue(self, btn: int, *, hold_ticks: int | None = None) -> None:
    if hold_ticks is None:
      hold_ticks = 3 if int(btn) == BTN_CANCEL else 2
    self._stw_queue.append(_StalkAction(int(btn), int(max(1, hold_ticks))))

  def _process_stalk_queue_10hz(self, CS, can_sends, now_nanos: int) -> None:
    # Keep a fresh idle seed while nothing is happening.
    if not self._stw_in_progress():
      self._stw_resync_seed_from_car(CS)
      return

    now = int(now_nanos)

    # Initialize / resync the 10Hz schedule using time, not frame.
    if self._stw_next_tick_ns is None:
      self._stw_next_tick_ns = now
    elif (now - int(self._stw_next_tick_ns)) > int(self._stw_tick_ns):
      # If we've been idle or delayed, restart cadence cleanly.
      self._stw_next_tick_ns = now

    if now < int(self._stw_next_tick_ns):
      return

    # Schedule the next tick; if we're far behind, jump forward.
    self._stw_next_tick_ns = int(self._stw_next_tick_ns) + int(self._stw_tick_ns)
    if int(self._stw_next_tick_ns) < (now - int(self._stw_tick_ns)):
      self._stw_next_tick_ns = now + int(self._stw_tick_ns)

    # Start next action (press phase)
    if (self._stw_active_ticks_left <= 0) and (self._stw_release_ticks_left <= 0) and self._stw_queue:
      act = self._stw_queue.pop(0)
      self._stw_active_btn = int(act.btn)
      self._stw_active_ticks_left = int(act.hold_ticks)
      self._stw_release_ticks_left = 1

      # Snapshot seed at the moment we start, but never from a non-idle loopback frame.
      msg = getattr(CS, "msg_stw_actn_req", None)
      if isinstance(msg, dict) and int(msg.get("SpdCtrlLvr_Stat", BTN_IDLE) or 0) == BTN_IDLE:
        self._stw_seed = dict(msg)
        self._stw_seed_bus_from_car = int(getattr(CS, "stw_actn_bus", CANBUS.party))
        self._stw_last_seed_frame = int(self.frame)

    btn_to_send = BTN_IDLE
    if self._stw_active_ticks_left > 0:
      btn_to_send = int(self._stw_active_btn)
      self._stw_active_ticks_left -= 1
    elif self._stw_release_ticks_left > 0:
      btn_to_send = BTN_IDLE
      self._stw_release_ticks_left -= 1

    # If the human is pressing a different stalk action, defer our send for this tick.
    cur_btn = int(getattr(CS, "cruise_buttons", 0) or 0)
    if (cur_btn != BTN_IDLE) and (cur_btn != int(btn_to_send)) and (cur_btn != int(self._stw_active_btn)):
      return

    if not self._stw_send_mirrored(CS, can_sends, btn_to_send):
      self._stw_active_ticks_left = 0
      self._stw_release_ticks_left = 0
      self._stw_queue.clear()
      if (self.frame % 200) == 0:
        cloudlog.info("[XNOR_STW] gated: missing CS.msg_stw_actn_req")
  def _speed_limit_sync(self, CC, CS) -> None:
    enabled = bool(getattr(CC, "enabled", False) or getattr(CC, "latActive", False))
    if not enabled:
      return

    if not self._cached_autopilot_disabled:
      return

    if not self._cached_adjust_acc_with_speed_limit:
      return

    if self._stw_in_progress():
      return

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

    if abs(diff_u) < 0.9:
      return

    if diff_u > 0:
      btn = BTN_UP2 if diff_u >= 4.5 else BTN_UP1
    else:
      btn = BTN_DOWN2 if diff_u <= -4.5 else BTN_DOWN1

    self._stw_enqueue(btn, hold_ticks=2)
    self._speed_sync_last_frame = int(self.frame)

    cloudlog.info(
      f"[XNOR_CRUISE_SYNC] uom={uom} target={target_ms*ms_to_u:.1f} current={current_ms*ms_to_u:.1f} "
      f"diff={diff_u:.1f} btn={btn}"
    )

  def update(self, CC, CS, now_nanos):
    actuators = CC.actuators
    can_sends = []

    self._refresh_cached_params()
    self._emit_internal_0x659(CS, can_sends)

    autopilot_disabled = bool(self._cached_autopilot_disabled)

    human_control = bool(getattr(CS, "human_control", False))

    op_enabled = bool(getattr(CC, "enabled", False) or getattr(CC, "latActive", False))
    if op_enabled and (not bool(self._op_enabled_prev)):
      if (autopilot_disabled and (self.CP.carFingerprint in LEGACY_CARS) and
          (float(getattr(CS.out, "vEgo", 0.0)) >= (18.0 * CV.MPH_TO_MS)) and
          (not bool(getattr(CS, "stock_cruise_enabled", False))) and
          (not self._stw_in_progress())):
        # HW2: RWD pull (2) held 2 ticks @10Hz, mirrored b0+b2 (Unity parity)
        self._stw_enqueue(BTN_MAIN, hold_ticks=2)
        cloudlog.info("[XNOR_CRUISE_ENGAGE] queued RWD engage (SpdCtrl=2)")

    self._op_enabled_prev = bool(op_enabled)

    # Decide any new speed-limit press (enqueue), then run the 10Hz sender.
    self._speed_limit_sync(CC, CS)
    self._process_stalk_queue_10hz(CS, can_sends, int(now_nanos))

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

# ===== ABSTRACT-SAFETY SHIM =====
# If an indentation/merge slip moves CarController.update() outside the class,
# Python will treat CarController as abstract and crash at startup. This shim
# installs a minimal compatible update() only when required.
import abc as _abc  # noqa: E402
import inspect as _inspect  # noqa: E402

def _cc_update_shim(self, CC, CS, now_nanos, *args, **kwargs):  # noqa: D401
  actuators = getattr(CC, "actuators", None) or CC.actuators
  can_sends = kwargs.get("can_sends") or kwargs.get("can_sends_in") or []
  try:
    f = getattr(self, "_refresh_cached_params", None)
    if callable(f):
      f()
    f = getattr(self, "_emit_internal_0x659", None)
    if callable(f):
      f(CS, can_sends)
  except Exception:
    pass

  new_actuators = actuators.as_builder()
  try:
    new_actuators.steeringAngleDeg = float(getattr(self, "apply_angle_last", 0.0))
  except Exception:
    pass

  try:
    self.frame = int(getattr(self, "frame", 0)) + 1
  except Exception:
    pass
  return new_actuators, can_sends

if _inspect.isabstract(CarController):  # pragma: no cover
  try:
    cloudlog.error(f"[XNOR] CarController abstract ({sorted(getattr(CarController, '__abstractmethods__', []))}); applying shim")
  except Exception:
    pass
  CarController.update = _cc_update_shim
  _abc.update_abstractmethods(CarController)
# ===== END ABSTRACT-SAFETY SHIM =====
