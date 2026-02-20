import copy
import math
from dataclasses import dataclass

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.car.modules.CFG_module import load_bool_param, load_float_param
from openpilot.selfdrive.car.modules.BLNK_module import BLNKController
from openpilot.selfdrive.car.modules.ALC_module import ALCController
from openpilot.selfdrive.car.modules.HSO_module import HSOController
from opendbc.can import CANDefine, CANParser
from opendbc.car import Bus, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarStateBase
from opendbc.car.tesla.values import DBC, CANBUS, GEAR_MAP, STEER_THRESHOLD, CAR, TeslaLegacyParams, LEGACY_CARS

ButtonType = structs.CarState.ButtonEvent.Type


@dataclass
class _TinklaConfig:
  autopilot_disabled: bool = False
  hands_on_level: float = 2.0
  adjust_acc_with_speed_limit: bool = False
  speed_limit_offset: float = 0.0
  speed_limit_use_relative: bool = False
  enable_alc: bool = True
  alc_delay: float = 0.75
  enable_hso: bool = True
  hso_numb_period: float = 1.5
  enable_acc: bool = False


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.msg_stw_actn_req = None
    self.stw_actn_bus = int(CANBUS.party)
    self.can_define = CANDefine(DBC[CP.carFingerprint][Bus.party])

    if self.CP.carFingerprint in LEGACY_CARS:
      if self.CP.carFingerprint == CAR.TESLA_MODEL_S_HW3:
        CANBUS.chassis = 1
        CANBUS.radar = 5
      elif self.CP.carFingerprint in (CAR.TESLA_MODEL_S_HW1, CAR.TESLA_MODEL_X_HW1, ):
        CANBUS.powertrain = CANBUS.party
        CANBUS.autopilot_powertrain = CANBUS.autopilot_party

      self.can_define_party = CANDefine(DBC[CP.carFingerprint][Bus.party])
      self.can_define_pt = CANDefine(DBC[CP.carFingerprint][Bus.pt])
      self.can_define_chassis = CANDefine(DBC[CP.carFingerprint][Bus.chassis])
      self.can_defines = {
        **self.can_define_party.dv,
        **self.can_define_pt.dv,
        **self.can_define_chassis.dv,
      }
      self.shifter_values = self.can_defines["DI_torque2"]["DI_gear"]
    else:
      self.shifter_values = self.can_define.dv["DI_systemStatus"]["DI_gear"]

    self.autopark = False
    self.autopark_prev = False
    self.cruise_enabled_prev = False

    self.hands_on_level = 0
    self.das_control = None
    # Unity parity fields
    self._tinkla = _TinklaConfig()
    self._param_frame = 0
    self.autopilot_disabled = False
    self.cruiseEnabled = False
    self._prev_cruise_buttons = 0
    self.cruise_buttons = 0
    self.turnSignalStalkState = 0
    self.speed_units = "MPH"
    self.speed_limit_ms = 0.0
    self.speed_limit_ms_das = 0.0
    self.stock_cruise_enabled = False
    self.stock_cruise_available = False
    self.stock_cruise_set_speed_ms = 0.0
    self.leftBlinkerLamp = False
    self.rightBlinkerLamp = False
    # ALC/BLNK/HSO/ACC (Unity parity)
    self.enableALC = bool(self._tinkla.enable_alc)
    self.autoStartAlcaDelay = float(self._tinkla.alc_delay)
    self.enableHSO = bool(self._tinkla.enable_hso)
    self.hsoNumbPeriod = float(self._tinkla.hso_numb_period)
    self.enableACC = bool(self._tinkla.enable_acc)

    self.tap_direction = 0
    self._alc_tap_latch_dir = 0
    self._alc_tap_latch_until = 0
    self.alca_direction = 0  # 0-none, 1-left, 2-right
    self.alca_pre_engage = False
    self.prev_alca_pre_engage = False
    self.alca_engaged = False
    self.alca_done = False
    self.alca_need_engagement = False

    self.HSOSteeringPressed = False
    self.human_control = False

    self.blinker_controller = BLNKController()
    self.alca_controller = ALCController()
    self.hso_controller = HSOController()
    try:
      self._reload_tinkla_params()
      self.autopilot_disabled = bool(self._tinkla.autopilot_disabled)
    except Exception:
      pass


  def _reload_tinkla_params(self) -> None:
    self._tinkla.autopilot_disabled = load_bool_param("TinklaAutopilotDisabled", False)
    self._tinkla.hands_on_level = load_float_param("TinklaHandsOnLevel", 2.0)
    self._tinkla.adjust_acc_with_speed_limit = load_bool_param("TinklaAdjustAccWithSpeedLimit", False)
    self._tinkla.speed_limit_offset = load_float_param("TinklaSpeedLimitOffset", 0.0)
    self._tinkla.speed_limit_use_relative = load_bool_param("TinklaSpeedLimitUseRelative", False)
    self._tinkla.enable_alc = load_bool_param("TinklaEnableALC", True)
    self._tinkla.alc_delay = load_float_param("TinklaAlcDelay", 0.75)
    self._tinkla.enable_hso = load_bool_param("TinklaEnableHSO", True)
    self._tinkla.hso_numb_period = load_float_param("TinklaHsoNumbPeriod", 1.5)
    self._tinkla.enable_acc = load_bool_param("TinklaEnableACC", False)
    self.enableALC = bool(self._tinkla.enable_alc)
    self.autoStartAlcaDelay = float(self._tinkla.alc_delay)
    self.enableHSO = bool(self._tinkla.enable_hso)
    self.hsoNumbPeriod = float(self._tinkla.hso_numb_period)
    self.enableACC = bool(self._tinkla.enable_acc)


  def _calc_speed_limit_target_ms(self, speed_units: str) -> float:
    limit_ms = float(getattr(self, "speed_limit_ms_das", 0.0) or getattr(self, "speed_limit_ms", 0.0) or 0.0)
    if limit_ms <= 0.0:
      return 0.0

    off = float(self._tinkla.speed_limit_offset)
    if self._tinkla.speed_limit_use_relative:
      return max(0.0, limit_ms * (1.0 + off / 100.0))

    if speed_units == "KPH":
      return max(0.0, limit_ms + off * CV.KPH_TO_MS)
    return max(0.0, limit_ms + off * CV.MPH_TO_MS)



  def _pick_stock_cruise_set_u(self, di_state: dict, v_ego_ms: float, cruise_enabled: bool, speed_units: str) -> tuple[float, str]:
    """Pick Tesla cruise setpoint in MPH/KPH without changing the DBC.

    - If stock cruise is disabled (XNOR lateral-only), DI_cruiseSet may look like ~0.5*vEgo.
      Prefer DI_digitalSpeed to keep the Comma UI sane.
    - If stock cruise is enabled, choose between DI_cruiseSet, DI_digitalSpeed, and DI_cruiseSet*2
      using plausibility + stability.
    """
    try:
      a = float(di_state.get("DI_cruiseSet", 0.0) or 0.0)
    except Exception:
      a = 0.0
    try:
      b = float(di_state.get("DI_digitalSpeed", 0.0) or 0.0)
    except Exception:
      b = 0.0

    uom = "KPH" if speed_units == "KPH" else "MPH"
    ms_to_u = CV.MS_TO_KPH if uom == "KPH" else CV.MS_TO_MPH
    v_u = float(v_ego_ms) * ms_to_u
    thr = max(2.5, 0.10 * max(v_u, 1.0))

    prev_enabled = bool(getattr(self, "_stock_cruise_enabled_prev", False))
    if bool(cruise_enabled) and (not prev_enabled) and a > 0.0:
      # On enable edge, setpoint typically equals current speed; detect half-scale once.
      if (b > 0.0) and (abs((2.0 * a) - b) <= thr) and (abs(a - b) > thr):
        self._cruise_set_scale = 2.0
      elif (abs((2.0 * a) - v_u) <= thr) and (abs(a - v_u) > thr):
        self._cruise_set_scale = 2.0
      elif (b > 0.0) and (abs(a - b) <= thr):
        self._cruise_set_scale = 1.0
      elif abs(a - v_u) <= thr:
        self._cruise_set_scale = 1.0

    scale = float(getattr(self, "_cruise_set_scale", 1.0) or 1.0)
    self._stock_cruise_enabled_prev = bool(cruise_enabled)

    if bool(cruise_enabled):
      if a > 0.0:
        val = a * scale
        src = "DI_cruiseSet" if scale < 1.5 else "DI_cruiseSet_x2"
        return float(val), src
      if b > 0.0:
        return float(b), "DI_digitalSpeed"
      return 0.0, "none"

    # Stock cruise disabled: keep UI stable by using digital speed (and correct half-scale if present).
    if b > 0.0:
      if (abs((2.0 * b) - v_u) <= thr) and (abs(b - v_u) > thr):
        return float(2.0 * b), "DI_digitalSpeed_x2"
      return float(b), "DI_digitalSpeed"

    if a > 0.0:
      # Fallback (rare): if DI_cruiseSet is the only non-zero, apply learned scale.
      val = a * scale
      src = "DI_cruiseSet" if scale < 1.5 else "DI_cruiseSet_x2"
      return float(val), src

    return 0.0, "none"



  @staticmethod
  def _decode_map_speed_limit_u(code: int) -> float:
    # UI_mapSpeedLimit enum fallback (units in mph/kph depending on UI_mapSpeedLimitUnits)
    table = {
      1: 5, 2: 7, 3: 10, 4: 15, 5: 20, 6: 25, 7: 30, 8: 35, 9: 40,
      10: 45, 11: 50, 12: 55, 13: 60, 14: 65, 15: 70, 16: 75, 17: 80,
      18: 85, 19: 90, 20: 95, 21: 100, 22: 105, 23: 110, 24: 115, 25: 120,
      26: 130, 27: 140, 28: 150, 29: 160,
    }
    return float(table.get(int(code), 0.0))

  def _update_speed_limit(self, can_parsers) -> None:
    """Unity-parity speed limit parsing (map/sign + DAS fallback) into m/s."""
    speed_limit_ms = 0.0
    speed_limit_ms_das = 0.0

    def _msg(name: str):
      for bk in (Bus.party, Bus.ap_party, Bus.cam, Bus.chassis, Bus.pt, Bus.ap_pt):
        cp = can_parsers.get(bk)
        if cp is None:
          continue
        try:
          return cp.vl[name]
        except KeyError:
          continue
      return None

    try:
      gps = _msg("UI_gpsVehicleSpeed")
      if gps is not None:
        msu = int(gps.get("UI_mapSpeedLimitUnits", 0))
        map_uom_to_ms = CV.KPH_TO_MS if msu == 1 else CV.MPH_TO_MS
        map_ms_to_uom = CV.MS_TO_KPH if msu == 1 else CV.MS_TO_MPH

        map_data = _msg("UI_driverAssistMapData") or {}
        speed_limit_type = int(map_data.get("UI_mapSpeedLimitType", map_data.get("UI_mapSpeedLimitType", map_data.get("UI_mapSpeedLimit", 0))) or 0)

        rd = _msg("UI_driverAssistRoadSign") or {}
        base_map = 0.0
        if int(rd.get("UI_roadSign", 0)) == 3:
          base_map = float(rd.get("UI_baseMapSpeedLimitMPS", 0.0) or 0.0)
          base_map = int(base_map * map_ms_to_uom + 0.99) / map_ms_to_uom

        if base_map > 0.0 and (speed_limit_type != 0x1F or base_map >= 5.56):
          speed_limit_ms = base_map
        else:
          mpp_u = float(gps.get("UI_mppSpeedLimit", 0.0) or 0.0)
          if mpp_u > 0.0:
            speed_limit_ms = mpp_u * map_uom_to_ms
          else:
            enum_u = self._decode_map_speed_limit_u(int(map_data.get("UI_mapSpeedLimit", 0) or 0))
            if enum_u > 0.0:
              speed_limit_ms = enum_u * map_uom_to_ms
    except Exception:
      pass

    try:
      ds2 = _msg("DAS_status2") or {}
      if isinstance(ds2, dict) and "DAS_accSpeedLimit" in ds2:
        speed_limit_ms_das = float(ds2.get("DAS_accSpeedLimit", 0.0) or 0.0) * CV.MPH_TO_MS
    except Exception:
      pass

    self.speed_limit_ms_das = float(speed_limit_ms_das)
    if speed_limit_ms_das > 0.0 and speed_limit_ms > 0.0:
      speed_limit_ms = min(speed_limit_ms, speed_limit_ms_das)
    self.speed_limit_ms = float(speed_limit_ms)

    if self._tinkla.adjust_acc_with_speed_limit and (self._param_frame % 100 == 0):
      try:
        uom = str(getattr(self, "speed_units", "MPH"))
        conv = 2.2369362920544 if uom == "MPH" else 3.6
        cloudlog.info(
          f"[XNOR_CS] uom={uom} src={getattr(self, '_cruise_set_src', 'none')} "
          f"cruiseSet={float(getattr(self, 'stock_cruise_set_speed_ms', 0.0))*conv:.1f} "
          f"stockCruise={bool(getattr(self, 'stock_cruise_enabled', False))} "
          f"speedLimit={float(getattr(self, 'speed_limit_ms', 0.0))*conv:.1f}"
        )
      except Exception:
        pass

  def update_autopark_state(self, autopark_state: str, cruise_enabled: bool):
    autopark_now = autopark_state in ("ACTIVE", "COMPLETE", "SELFPARK_STARTED")
    if autopark_now and not self.autopark_prev and not self.cruise_enabled_prev:
      self.autopark = True
    if not autopark_now:
      self.autopark = False
    self.autopark_prev = autopark_now
    self.cruise_enabled_prev = cruise_enabled


  def update(self, can_parsers) -> structs.CarState:
    if self.CP.carFingerprint in LEGACY_CARS:
      return self.update_legacy(can_parsers)

    cp_party = can_parsers[Bus.party]
    cp_ap_party = can_parsers[Bus.ap_party]
    ret = structs.CarState()

    # Vehicle speed
    ret.vEgoRaw = cp_party.vl["DI_speed"]["DI_vehicleSpeed"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)

    # Gas pedal
    ret.gasPressed = cp_party.vl["DI_systemStatus"]["DI_accelPedalPos"] > 0

    # Brake pedal
    ret.brake = 0
    ret.brakePressed = cp_party.vl["ESP_status"]["ESP_driverBrakeApply"] == 2

    # Steering wheel
    epas_status = cp_party.vl["EPAS3S_sysStatus"]
    self.hands_on_level = epas_status["EPAS3S_handsOnLevel"]
    ret.steeringAngleDeg = -epas_status["EPAS3S_internalSAS"]
    ret.steeringRateDeg = -cp_ap_party.vl["SCCM_steeringAngleSensor"]["SCCM_steeringAngleSpeed"]
    ret.steeringTorque = -epas_status["EPAS3S_torsionBarTorque"]

    # stock handsOnLevel uses >0.5 for 0.25s, but is too slow
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > STEER_THRESHOLD, 5)

    eac_status = self.can_define.dv["EPAS3S_sysStatus"]["EPAS3S_eacStatus"].get(int(epas_status["EPAS3S_eacStatus"]), None)
    ret.steerFaultPermanent = eac_status == "EAC_FAULT"
    ret.steerFaultTemporary = eac_status == "EAC_INHIBITED"

    # FSD disengages using union of handsOnLevel (slow overrides) and high angle rate faults (fast overrides, high speed)
    eac_error_code = self.can_define.dv["EPAS3S_sysStatus"]["EPAS3S_eacErrorCode"].get(int(epas_status["EPAS3S_eacErrorCode"]), None)
    if self.enableHSO:
      ret.steeringDisengage = (eac_status == "EAC_INHIBITED" and
                                                         eac_error_code == "EAC_ERROR_HIGH_ANGLE_RATE_SAFETY")
    else:
      ret.steeringDisengage = self.hands_on_level >= 3 or (eac_status == "EAC_INHIBITED" and
                                                         eac_error_code == "EAC_ERROR_HIGH_ANGLE_RATE_SAFETY")

    # Cruise state
    cruise_state = self.can_define.dv["DI_state"]["DI_cruiseState"].get(int(cp_party.vl["DI_state"]["DI_cruiseState"]), None)
    speed_units = self.can_define.dv["DI_state"]["DI_speedUnits"].get(int(cp_party.vl["DI_state"]["DI_speedUnits"]), None)

    autopark_state = self.can_define.dv["DI_state"]["DI_autoparkState"].get(int(cp_party.vl["DI_state"]["DI_autoparkState"]), None)
    cruise_enabled = cruise_state in ("ENABLED", "STANDSTILL", "OVERRIDE", "PRE_FAULT", "PRE_CANCEL")
    self.update_autopark_state(autopark_state, cruise_enabled)
    # Cruise set speed (DI_state): pick correct decoded field without changing the DBC
    uom = speed_units if speed_units in ("KPH", "MPH") else "MPH"
    cruise_set_u, src = self._pick_stock_cruise_set_u(cp_party.vl["DI_state"], float(ret.vEgo), bool(cruise_enabled), uom)
    self.stock_cruise_enabled = bool(cruise_enabled)
    self.stock_cruise_available = bool(cruise_state == "STANDBY" or cruise_enabled)
    if cruise_set_u > 0.0:
      self.stock_cruise_set_speed_ms = float(cruise_set_u) * (CV.KPH_TO_MS if uom == "KPH" else CV.MPH_TO_MS)
      ret.cruiseState.speed = max(float(self.stock_cruise_set_speed_ms), 1e-3)
    else:
      self.stock_cruise_set_speed_ms = 0.0
      ret.cruiseState.speed = max(float(ret.vEgo), 1e-3)
    self._cruise_set_src = str(src)
    if self.autopilot_disabled:
      # Unity parity: allow engagement without Tesla cruise (low-speed lateral only)
      ret.cruiseState.available = True
      ret.cruiseState.enabled = bool(self.cruiseEnabled)
    else:
      ret.cruiseState.enabled = cruise_enabled and not self.autopark
      ret.cruiseState.available = cruise_state == "STANDBY" or ret.cruiseState.enabled
    ret.cruiseState.standstill = False  # This needs to be false, since we can resume from stop without sending anything special
    ret.standstill = cruise_state == "STANDSTILL"
    ret.accFaulted = cruise_state == "FAULT"

    # Unity parity: store last STW_ACTN_RQ for virtual stalk + tap-to-ALC
    self.speed_units = speed_units if speed_units in ("KPH", "MPH") else "MPH"

    stw = None
    stw_bus = None
    for bk in (Bus.party, Bus.chassis, Bus.pt, Bus.ap_party, Bus.ap_pt):
      _cp = can_parsers.get(bk)
      if _cp is None:
        continue
      try:
        stw = _cp.vl["STW_ACTN_RQ"]
        stw_bus = int(getattr(_cp, "bus", CANBUS.party))
        break
      except KeyError:
        continue
    if stw is not None:
      self.msg_stw_actn_req = copy.copy(stw)
      if stw_bus is not None:
        self.stw_actn_bus = int(stw_bus)
      self.cruise_buttons = int(stw.get("SpdCtrlLvr_Stat", 0))
      raw_ts = int(stw.get("TurnIndLvr_Stat", 0))
      self.turnSignalStalkState = 0 if raw_ts == 3 else raw_ts
    else:
      self.cruise_buttons = 0
      self.turnSignalStalkState = 0
      self.tap_direction = 0
      self.blinker_controller.tap_direction = 0

    if self.autopilot_disabled:
      if self.cruise_buttons == 2:  # MAIN
        self.cruiseEnabled = True
      if self.cruise_buttons == 1:  # CANCEL
        self.cruiseEnabled = False


    # Gear
    ret.gearShifter = GEAR_MAP[self.can_define.dv["DI_systemStatus"]["DI_gear"].get(int(cp_party.vl["DI_systemStatus"]["DI_gear"]), "DI_GEAR_INVALID")]

    # Doors
    ret.doorOpen = cp_party.vl["UI_warning"]["anyDoorOpen"] == 1

    # Blinkers
    # Blinkers: modern Teslas report 1=blinking (stalk released), 2=stalk held
    # Unity ALC uses tap-to-change; expose only "tap/comfort" blink to DesireHelper
    self.leftBlinkerLamp = cp_party.vl["UI_warning"]["leftBlinkerBlinking"] != 0
    self.rightBlinkerLamp = cp_party.vl["UI_warning"]["rightBlinkerBlinking"] != 0

    self.blinker_controller.update_state(self, self._param_frame)
    self.tap_direction = int(self.blinker_controller.tap_direction)

    # Unity parity: latch tap/comfort blinkers so DesireHelper sees continuous one_blinker during auto-start delay
    if self.enableALC and (self.turnSignalStalkState == 0):
      one = (self.leftBlinkerLamp != self.rightBlinkerLamp)
      if one and int(getattr(self, '_alc_tap_latch_until', 0)) <= self._param_frame:
        self._alc_tap_latch_dir = 1 if self.leftBlinkerLamp else 2
        dur_s = max(2.5, float(self.autoStartAlcaDelay) + 0.5)
        self._alc_tap_latch_until = int(self._param_frame + dur_s * 100)
      if int(getattr(self, '_alc_tap_latch_until', 0)) > self._param_frame:
        ret.leftBlinker = (self._alc_tap_latch_dir == 1)
        ret.rightBlinker = (self._alc_tap_latch_dir == 2)
      else:
        ret.leftBlinker = False
        ret.rightBlinker = False
    else:
      # stock behavior (incl. full stalk)
      ret.leftBlinker = self.leftBlinkerLamp
      ret.rightBlinker = self.rightBlinkerLamp

    # HSO (Unity parity): use handsOnLevel for steeringPressed when enabled, but never during blinkers (preserve ALC)
    self.HSOSteeringPressed = bool(getattr(self, "hands_on_level", 0.0) >= float(self._tinkla.hands_on_level))
    if self.enableHSO and not (ret.leftBlinker or ret.rightBlinker):
      ret.steeringPressed = self.HSOSteeringPressed

    # Seatbelt
    ret.seatbeltUnlatched = cp_party.vl["UI_warning"]["buckleStatus"] != 1

    # Blindspot
    ret.leftBlindspot = cp_ap_party.vl["DAS_status"]["DAS_blindSpotRearLeft"] != 0
    ret.rightBlindspot = cp_ap_party.vl["DAS_status"]["DAS_blindSpotRearRight"] != 0

    # Speed limit best-effort (needed for speed-limit matching)
    self._update_speed_limit(can_parsers)


    # AEB
    ret.stockAeb = cp_ap_party.vl["DAS_control"]["DAS_aebEvent"] == 1

    # LKAS
    ret.stockLkas = cp_ap_party.vl["DAS_steeringControl"]["DAS_steeringControlType"] == 2  # LANE_KEEP_ASSIST

    # Stock Autosteer should be off (includes FSD)
    if self.CP.carFingerprint in (CAR.TESLA_MODEL_3, CAR.TESLA_MODEL_Y, CAR.TESLA_MODEL_Y_JUNIPER):
      ret.invalidLkasSetting = cp_ap_party.vl["DAS_settings"]["DAS_autosteerEnabled"] != 0
    else:
      pass
    # Buttons # ToDo: add Gap adjust button

    # Messages needed by carcontroller
    self.das_control = copy.copy(cp_ap_party.vl["DAS_control"])

    # Unity parity tail
    if (self._param_frame % 100) == 0:
      try:
        self._reload_tinkla_params()
        self.autopilot_disabled = bool(self._tinkla.autopilot_disabled)
        self.enableHSO = bool(getattr(self._tinkla, 'enable_hso', True))
        self.hsoNumbPeriod = float(getattr(self._tinkla, 'hso_numb_period', 1.5) or 1.5)
        self.handsOnLimit = float(getattr(self._tinkla, 'hands_on_level', 2.0) or 2.0)
      except Exception:
        pass
    self._param_frame += 1

    if self.autopilot_disabled:
      ret.cruiseState.available = True
      ret.cruiseState.enabled = bool(self.cruiseEnabled) and (not ret.doorOpen) and (ret.gearShifter == structs.CarState.GearShifter.drive) and (not ret.seatbeltUnlatched)
      self.cruiseEnabled = bool(ret.cruiseState.enabled)

    ret.buttonEvents = []
    try:
      prev = int(self._prev_cruise_buttons)
      cur = int(getattr(self, "cruise_buttons", 0))
      def _be(t, pressed):
        e = structs.CarState.ButtonEvent()
        e.type = t
        e.pressed = pressed
        return e
      accel_vals = (4, 16)
      decel_vals = (8, 32)
      if (prev in accel_vals) and (cur not in accel_vals): ret.buttonEvents.append(_be(ButtonType.accelCruise, False))
      if (prev in decel_vals) and (cur not in decel_vals): ret.buttonEvents.append(_be(ButtonType.decelCruise, False))
      if (prev == 1) and (cur != 1): ret.buttonEvents.append(_be(ButtonType.cancel, False))
      if (prev == 2) and (cur != 2): ret.buttonEvents.append(_be(ButtonType.resumeCruise, False))
      self._prev_cruise_buttons = cur
    except Exception:
      pass


    return ret


  def update_legacy(self, can_parsers) -> structs.CarState:
    cp_party = can_parsers[Bus.party]
    cp_ap_party = can_parsers[Bus.ap_party]
    cp_pt = can_parsers[Bus.pt]
    cp_ap_pt = can_parsers[Bus.ap_pt]
    cp_chassis = can_parsers[Bus.chassis]
    ret = structs.CarState()

    # Vehicle speed
    ret.vEgoRaw = cp_chassis.vl["ESP_B"]["ESP_vehicleSpeed"] * CV.KPH_TO_MS
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)

    # Gas pedal
    ret.gasPressed = cp_pt.vl["DI_torque1"]["DI_pedalPos"] > 0

    # Brake pedal
    ret.brake = 0
    ret.brakePressed = cp_chassis.vl["BrakeMessage"]["driverBrakeStatus"] == 2

    # Steering wheel
    if self.CP.carFingerprint == CAR.TESLA_MODEL_S_HW3:
      epas_status = cp_party.vl["EPAS_sysStatus"]
    else:
      epas_status = cp_chassis.vl["EPAS_sysStatus"]
    self.hands_on_level = epas_status["EPAS_handsOnLevel"]
    ret.steeringAngleDeg = -epas_status["EPAS_internalSAS"]
    ret.steeringRateDeg = -cp_chassis.vl["STW_ANGLHP_STAT"]["StW_AnglHP_Spd"]
    ret.steeringTorque = -epas_status["EPAS_torsionBarTorque"]

    # stock handsOnLevel uses >0.5 for 0.25s, but is too slow
    ret.steeringPressed = self.update_steering_pressed(abs(ret.steeringTorque) > STEER_THRESHOLD, 5)

    eac_status = self.can_defines["EPAS_sysStatus"]["EPAS_eacStatus"].get(int(epas_status["EPAS_eacStatus"]), None)
    ret.steerFaultPermanent = eac_status == "EAC_FAULT"
    ret.steerFaultTemporary = eac_status == "EAC_INHIBITED"

    # FSD disengages using union of handsOnLevel (slow overrides) and high angle rate faults (fast overrides, high speed)
    eac_error_code = self.can_defines["EPAS_sysStatus"]["EPAS_eacErrorCode"].get(int(epas_status["EPAS_eacErrorCode"]), None)
    if self.enableHSO:
      ret.steeringDisengage = (eac_status == "EAC_INHIBITED" and
                                                         eac_error_code == "EAC_ERROR_HIGH_ANGLE_RATE_SAFETY")
    else:
      ret.steeringDisengage = self.hands_on_level >= 3 or (eac_status == "EAC_INHIBITED" and
                                                         eac_error_code == "EAC_ERROR_HIGH_ANGLE_RATE_SAFETY")

    # Cruise state
    cruise_state = self.can_defines["DI_state"]["DI_cruiseState"].get(int(cp_chassis.vl["DI_state"]["DI_cruiseState"]), None)
    speed_units = self.can_defines["DI_state"]["DI_speedUnits"].get(int(cp_chassis.vl["DI_state"]["DI_speedUnits"]), None)

    cruise_enabled = cruise_state in ("ENABLED", "STANDSTILL", "OVERRIDE", "PRE_FAULT", "PRE_CANCEL")
    # Cruise set speed (DI_state): pick correct decoded field without changing the DBC
    ret.cruiseState.enabled = cruise_enabled
    uom = speed_units if speed_units in ("KPH", "MPH") else "MPH"
    cruise_set_u, src = self._pick_stock_cruise_set_u(cp_chassis.vl["DI_state"], float(ret.vEgo), bool(cruise_enabled), uom)
    self.stock_cruise_enabled = bool(cruise_enabled)
    self.stock_cruise_available = bool(cruise_state == "STANDBY" or cruise_enabled)
    if cruise_set_u > 0.0:
      self.stock_cruise_set_speed_ms = float(cruise_set_u) * (CV.KPH_TO_MS if uom == "KPH" else CV.MPH_TO_MS)
      ret.cruiseState.speed = max(float(self.stock_cruise_set_speed_ms), 1e-3)
    else:
      self.stock_cruise_set_speed_ms = 0.0
      ret.cruiseState.speed = max(float(ret.vEgo), 1e-3)
    self._cruise_set_src = str(src)
    ret.cruiseState.available = cruise_state == "STANDBY" or ret.cruiseState.enabled
    ret.cruiseState.standstill = False  # This needs to be false, since we can resume from stop without sending anything special
    ret.standstill = cruise_state == "STANDSTILL"
    ret.accFaulted = cruise_state == "FAULT"

    # Unity parity: store last STW_ACTN_RQ for virtual stalk + tap-to-ALC
    self.speed_units = speed_units if speed_units in ("KPH", "MPH") else "MPH"

    stw = None
    stw_bus = None
    for bk in (Bus.party, Bus.chassis, Bus.pt, Bus.ap_party, Bus.ap_pt):
      _cp = can_parsers.get(bk)
      if _cp is None:
        continue
      try:
        stw = _cp.vl["STW_ACTN_RQ"]
        stw_bus = int(getattr(_cp, "bus", CANBUS.party))
        break
      except KeyError:
        continue
    if stw is not None:
      self.msg_stw_actn_req = copy.copy(stw)
      if stw_bus is not None:
        self.stw_actn_bus = int(stw_bus)
      self.cruise_buttons = int(stw.get("SpdCtrlLvr_Stat", 0))
      raw_ts = int(stw.get("TurnIndLvr_Stat", 0))
      self.turnSignalStalkState = 0 if raw_ts == 3 else raw_ts
    else:
      self.cruise_buttons = 0
      self.turnSignalStalkState = 0
      self.tap_direction = 0
      self.blinker_controller.tap_direction = 0


    if self.autopilot_disabled:
      if self.cruise_buttons == 2:  # MAIN
        self.cruiseEnabled = True
      if self.cruise_buttons == 1:  # CANCEL
        self.cruiseEnabled = False


    # Gear
    ret.gearShifter = GEAR_MAP[self.can_defines["DI_torque2"]["DI_gear"].get(int(cp_chassis.vl["DI_torque2"]["DI_gear"]), "DI_GEAR_INVALID")]

    # Doors
    DOORS = ["DOOR_STATE_FL", "DOOR_STATE_FR", "DOOR_STATE_RL", "DOOR_STATE_RR", "DOOR_STATE_FrontTrunk", "BOOT_STATE"]
    ret.doorOpen = any((self.can_defines["GTW_carState"][door].get(int(cp_chassis.vl["GTW_carState"][door]), "OPEN") == "OPEN") for door in DOORS)

    # Blinkers
    self.leftBlinkerLamp = cp_chassis.vl["GTW_carState"]["BC_indicatorLStatus"] == 1
    self.rightBlinkerLamp = cp_chassis.vl["GTW_carState"]["BC_indicatorRStatus"] == 1

    self.blinker_controller.update_state(self, self._param_frame)
    self.tap_direction = int(self.blinker_controller.tap_direction)

    # Unity parity: latch tap/comfort blinkers so DesireHelper sees continuous one_blinker during auto-start delay
    if self.enableALC and (self.turnSignalStalkState == 0):
      one = (self.leftBlinkerLamp != self.rightBlinkerLamp)
      if one and int(getattr(self, '_alc_tap_latch_until', 0)) <= self._param_frame:
        self._alc_tap_latch_dir = 1 if self.leftBlinkerLamp else 2
        dur_s = max(2.5, float(self.autoStartAlcaDelay) + 0.5)
        self._alc_tap_latch_until = int(self._param_frame + dur_s * 100)
      if int(getattr(self, '_alc_tap_latch_until', 0)) > self._param_frame:
        ret.leftBlinker = (self._alc_tap_latch_dir == 1)
        ret.rightBlinker = (self._alc_tap_latch_dir == 2)
      else:
        ret.leftBlinker = False
        ret.rightBlinker = False
    else:
      # stock behavior (incl. full stalk)
      ret.leftBlinker = self.leftBlinkerLamp
      ret.rightBlinker = self.rightBlinkerLamp

    # HSO (Unity parity): use handsOnLevel for steeringPressed when enabled, but never during blinkers (preserve ALC)
    self.HSOSteeringPressed = bool(getattr(self, "hands_on_level", 0.0) >= float(self._tinkla.hands_on_level))
    if self.enableHSO and not (ret.leftBlinker or ret.rightBlinker):
      ret.steeringPressed = self.HSOSteeringPressed

    # Seatbelt
    if self.CP.flags & TeslaLegacyParams.NO_SDM1:
      ret.seatbeltUnlatched = cp_chassis.vl["RCM_status"]["RCM_buckleDriverStatus"] != 1
    else:
      ret.seatbeltUnlatched = cp_chassis.vl["SDM1"]["SDM_bcklDrivStatus"] != 1

    if (self._param_frame % 100) == 0:
      try:
        self._reload_tinkla_params()
        self.autopilot_disabled = bool(self._tinkla.autopilot_disabled)
        self.enableHSO = bool(getattr(self._tinkla, 'enable_hso', True))
        self.hsoNumbPeriod = float(getattr(self._tinkla, 'hso_numb_period', 1.5) or 1.5)
        self.handsOnLimit = float(getattr(self._tinkla, 'hands_on_level', 2.0) or 2.0)
      except Exception:
        pass
    self._param_frame += 1

    if self.autopilot_disabled:
      ret.cruiseState.available = True
      ret.cruiseState.enabled = bool(self.cruiseEnabled) and (not ret.doorOpen) and (ret.gearShifter == structs.CarState.GearShifter.drive) and (not ret.seatbeltUnlatched)
      self.cruiseEnabled = bool(ret.cruiseState.enabled)

    # AEB
    ret.stockAeb = cp_ap_pt.vl["DAS_control"]["DAS_aebEvent"] == 1

    # LKAS
    ret.stockLkas = cp_ap_party.vl["DAS_steeringControl"]["DAS_steeringControlType"] == 2  # LANE_KEEP_ASSIST

    # Stock Autosteer should be off (includes FSD)
    # ret.invalidLkasSetting = cp_ap_party.vl["DAS_settings"]["DAS_autosteerEnabled"] != 0

    # Buttons # ToDo: add Gap adjust button

    # Messages needed by carcontroller
    self.das_control = copy.copy(cp_ap_pt.vl["DAS_control"])


    return ret


  @staticmethod
  def get_can_parsers(CP):
    if CP.carFingerprint in LEGACY_CARS:
      return {
        Bus.party: CANParser(DBC[CP.carFingerprint][Bus.party], [], CANBUS.party),
        Bus.ap_party: CANParser(DBC[CP.carFingerprint][Bus.party], [], CANBUS.autopilot_party),
        Bus.pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], CANBUS.powertrain),
        Bus.ap_pt: CANParser(DBC[CP.carFingerprint][Bus.pt], [], CANBUS.autopilot_powertrain),
        Bus.chassis: CANParser(DBC[CP.carFingerprint][Bus.chassis], [], CANBUS.chassis if CP.carFingerprint == CAR.TESLA_MODEL_S_HW3 else CANBUS.party),
        Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.party], [], CANBUS.party + 4),
      }

    return {
      Bus.party: CANParser(DBC[CP.carFingerprint][Bus.party], [], CANBUS.party),
      Bus.ap_party: CANParser(DBC[CP.carFingerprint][Bus.party], [], CANBUS.autopilot_party),
      Bus.cam: CANParser(DBC[CP.carFingerprint][Bus.party], [], CANBUS.party + 4),
    }
