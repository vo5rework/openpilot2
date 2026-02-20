from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.tesla.values import CANBUS, CarControllerParams
def _crc8_11d(data: bytes) -> int:
  """CRC8 poly 0x11D, init 0x00, xorout 0xFF, not reflected (Unity parity)."""
  crc = 0x00
  for b in data:
    crc ^= b
    for _ in range(8):
      crc = ((crc << 1) ^ 0x11D) if (crc & 0x80) else (crc << 1)
      crc &= 0xFF
  return crc ^ 0xFF


def create_fake_das_msg(pedal_enabled: bool,
                        autopilot_disabled: bool,
                        bus: int,
                        stalk_main: bool = False,
                        stalk_cancel: bool = False):
  """Internal openpilot->panda carrier (0x659). Safety consumes + blocks it from the car.

  Byte5 bits:
    bit7: autopilot_disabled
    bit5: pedal_enabled
    bit1: stalk_main (edge)
    bit0: stalk_cancel (edge)
  """
  dat = bytearray(8)
  dat[5] = ((0x20 if pedal_enabled else 0) |
            (0x80 if autopilot_disabled else 0) |
            (0x02 if stalk_main else 0) |
            (0x01 if stalk_cancel else 0))
  return (0x659, bytes(dat), bus)


def create_fake_das_message(pedal_enabled: bool,
                            autopilot_disabled: bool,
                            *,
                            stalk_main: bool = False,
                            stalk_cancel: bool = False,
                            bus: int = 0):
  """Compatibility alias for forks expecting create_fake_das_message()."""
  return create_fake_das_msg(pedal_enabled, autopilot_disabled, bus,
                             stalk_main=stalk_main, stalk_cancel=stalk_cancel)

class TeslaCAN:
  def _create_fake_das(self, pedal_enabled: bool, autopilot_disabled: bool, bus: int,
                       stalk_main: bool = False, stalk_cancel: bool = False):
    """Compatibility wrapper expected by some Tesla controller forks."""
    return create_fake_das_msg(pedal_enabled, autopilot_disabled, bus,
                               stalk_main=stalk_main, stalk_cancel=stalk_cancel)

  def __init__(self, packer):
    self.packer = packer

  def create_steering_control(self, angle, enabled):
    values = {
      "DAS_steeringAngleRequest": -angle,
      "DAS_steeringHapticRequest": 0,
      "DAS_steeringControlType": 1 if enabled else 0,
    }

    return self.packer.make_can_msg("DAS_steeringControl", CANBUS.party, values)

  def create_longitudinal_command(self, acc_state, accel, counter, v_ego, active, set_speed_kph: float | None = None):
    from opendbc.car.interfaces import V_CRUISE_MAX

    if set_speed_kph is not None:
      set_speed = float(max(0.0, min(float(set_speed_kph), V_CRUISE_MAX)))
    else:
      set_speed = max(v_ego * CV.MS_TO_KPH, 0.0)
      if active:
        # TODO: this causes jerking after gas override when above set speed
        set_speed = 0.0 if accel < 0 else V_CRUISE_MAX

    values = {
      "DAS_setSpeed": set_speed,
      "DAS_accState": acc_state,
      "DAS_aebEvent": 0,
      "DAS_jerkMin": CarControllerParams.JERK_LIMIT_MIN,
      "DAS_jerkMax": CarControllerParams.JERK_LIMIT_MAX,
      "DAS_accelMin": accel,
      "DAS_accelMax": max(accel, 0),
      "DAS_controlCounter": counter,
    }
    return self.packer.make_can_msg("DAS_control", CANBUS.party, values)
  def create_steering_allowed(self, counter):
    values = {
      "APS_eacAllow": 1,
    }

    return self.packer.make_can_msg("APS_eacMonitor", CANBUS.party, values)

  def create_action_request(self, bus: int, msg_stw_actn_req: dict, cruise_button: int) -> tuple[int, int, bytes]:
    """Create STW_ACTN_RQ to emulate cruise stalk up/down/cancel (Unity parity)."""
    if msg_stw_actn_req is None:
      msg_stw_actn_req = {}
    values = dict(msg_stw_actn_req)
    values["SpdCtrlLvr_Stat"] = int(cruise_button)
    counter = (int(values.get("MC_STW_ACTN_RQ", 0)) + 1) % 16
    values["MC_STW_ACTN_RQ"] = counter
    values["CRC_STW_ACTN_RQ"] = 0

    msg = self.packer.make_can_msg("STW_ACTN_RQ", bus, values)
    # msg[1] is bytes payload
    dat = msg[1]
    crc = _crc8_11d(dat[:7])
    values["CRC_STW_ACTN_RQ"] = crc
    return self.packer.make_can_msg("STW_ACTN_RQ", bus, values)


  def create_fake_das_msg(self, pedalEnabled: bool, autopilot_disabled: bool, bus: int = CANBUS.party, *,


                          stalk_main: bool = False, stalk_cancel: bool = False):


    """Internal openpilot->panda msg (0x659). Safety consumes + blocks it from the car.


    Byte5 bits: bit7=autopilot_disabled, bit5=pedalEnabled, bit1=stalk_main(edge), bit0=stalk_cancel(edge)


    """


    dat = bytearray(8)


    dat[5] = ((0x20 if pedalEnabled else 0) |


              (0x80 if autopilot_disabled else 0) |


              (0x02 if stalk_main else 0) |


              (0x01 if stalk_cancel else 0))


    return (0x659, bytes(dat), bus)



def tesla_checksum(address: int, sig, d: bytearray) -> int:
  """Checksum used by opendbc dbc packer for Tesla frames."""
  checksum = (address & 0xFF) + ((address >> 8) & 0xFF)
  checksum_byte = sig.start_bit // 8
  for i in range(len(d)):
    if i != checksum_byte:
      checksum += d[i]
  return checksum & 0xFF

