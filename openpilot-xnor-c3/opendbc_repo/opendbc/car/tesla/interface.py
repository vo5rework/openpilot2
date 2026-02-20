from opendbc.car import get_safety_config, structs
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.tesla.carcontroller import CarController
from opendbc.car.tesla.carstate import CarState
from opendbc.car.tesla.values import TeslaSafetyFlags, CAR, TeslaLegacyParams, LEGACY_CARS
from opendbc.car.tesla.radar_interface import RadarInterface
from openpilot.common.params import Params
from openpilot.selfdrive.car.modules.HSO_module import HSOController
import cereal.messaging as messaging


class CarInterface(CarInterfaceBase):
  CarState = CarState
  CarController = CarController
  RadarInterface = RadarInterface

  def __init__(self, CP: structs.CarParams):
    super().__init__(CP)
    self._model_v2_sock = messaging.sub_sock('modelV2', conflate=True)

  def post_update(self, c: structs.CarControl, ret: structs.CarState) -> None:
    # HSO (Unity parity)
    try:
      self.CS.human_control = bool(self.CS.hso_controller.update_stat(self.CS, bool(getattr(c, 'latActive', False)), c.actuators, int(self.CS._param_frame)))
    except Exception:
      self.CS.human_control = False

    # ALC: update model lane change state and inject sign-correct torque for tap-only ALC
    if not getattr(self.CS, 'enableALC', False):
      return
    model_msg = messaging.recv_one_or_none(self._model_v2_sock)
    try:
      self.CS.alca_controller.update(bool(getattr(c, 'latActive', False)), self.CS, int(self.CS._param_frame), model_msg)
    except Exception:
      return
    if getattr(self.CS, 'alca_need_engagement', False):
      ret.steeringPressed = True
      if int(getattr(self.CS, 'alca_direction', 0)) == 1:
        ret.steeringTorque = 2.0
      elif int(getattr(self.CS, 'alca_direction', 0)) == 2:
        ret.steeringTorque = -2.0

  @staticmethod
  def _get_params(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    if candidate in LEGACY_CARS:
      return CarInterface._get_params_sx(ret, candidate, fingerprint, car_fw, alpha_long, is_release, docs)

    ret.brand = "tesla"

    ret.safetyConfigs = [get_safety_config(structs.CarParams.SafetyModel.tesla)]

    # Unity parity: enable stalk->controlsAllowed contract via safetyParam bit
    if Params().get_bool("TinklaAutopilotDisabled"):
      for cfg in ret.safetyConfigs:
        cfg.safetyParam |= int(TeslaSafetyFlags.OP_STALK_ENABLE)

    ret.steerLimitTimer = 0.4
    ret.steerActuatorDelay = 0.1
    ret.steerAtStandstill = True

    ret.steerControlType = structs.CarParams.SteerControlType.angle
    ret.radarUnavailable = True

    ret.alphaLongitudinalAvailable = True
    if alpha_long:
      ret.openpilotLongitudinalControl = True
      ret.safetyConfigs[0].safetyParam |= TeslaSafetyFlags.LONG_CONTROL.value

      ret.vEgoStopping = 0.1
      ret.vEgoStarting = 0.1
      ret.stoppingDecelRate = 0.3

    return ret

  @staticmethod
  def _get_params_sx(ret: structs.CarParams, candidate, fingerprint, car_fw, alpha_long, is_release, docs) -> structs.CarParams:
    ret.brand = "tesla"

    if not any(0x201 in f for f in fingerprint.values()):
      ret.flags |= TeslaLegacyParams.NO_SDM1.value

    if candidate in (CAR.TESLA_MODEL_S_HW1, CAR.TESLA_MODEL_X_HW1, ):
      ret.safetyConfigs = [
        get_safety_config(structs.CarParams.SafetyModel.teslaLegacy, int(TeslaSafetyFlags.FLAG_HW1)),
      ]
    elif candidate in (CAR.TESLA_MODEL_S_HW2,):
      ret.safetyConfigs = [
        get_safety_config(structs.CarParams.SafetyModel.teslaLegacy, int(TeslaSafetyFlags.FLAG_HW2)),
        get_safety_config(structs.CarParams.SafetyModel.teslaLegacy, int(TeslaSafetyFlags.FLAG_HW2 | TeslaSafetyFlags.FLAG_EXTERNAL_PANDA)),
      ]
    elif candidate in (CAR.TESLA_MODEL_S_HW3,):
      ret.safetyConfigs = [
        get_safety_config(structs.CarParams.SafetyModel.teslaLegacy, int(TeslaSafetyFlags.FLAG_HW3)),
        get_safety_config(structs.CarParams.SafetyModel.teslaLegacy, int(TeslaSafetyFlags.FLAG_HW3 | TeslaSafetyFlags.FLAG_EXTERNAL_PANDA)),
      ]

    # Unity parity: enable stalk->controlsAllowed contract via safetyParam bit
    if Params().get_bool("TinklaAutopilotDisabled"):
      for cfg in ret.safetyConfigs:
        cfg.safetyParam |= int(TeslaSafetyFlags.OP_STALK_ENABLE)

    ret.steerLimitTimer = 0.4
    ret.steerActuatorDelay = 0.1
    ret.steerAtStandstill = True

    ret.steerControlType = structs.CarParams.SteerControlType.angle
    ret.radarUnavailable = candidate in (CAR.TESLA_MODEL_S_HW2, )

    ret.alphaLongitudinalAvailable = True
    ret.openpilotLongitudinalControl = True
    ret.safetyConfigs[0].safetyParam |= TeslaSafetyFlags.LONG_CONTROL.value

    ret.vEgoStopping = 0.1
    ret.vEgoStarting = 0.1
    ret.stoppingDecelRate = 0.3

    return ret
