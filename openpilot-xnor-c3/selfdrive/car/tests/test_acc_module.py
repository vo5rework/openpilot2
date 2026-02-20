from types import SimpleNamespace

from openpilot.selfdrive.car.modules.ACC_module import ACCController
from opendbc.car.tesla.values import CruiseButtons
from opendbc.car.common.conversions import Conversions as CV


def _build_cs(*, speed_units="KPH", v_ego_ms=25.0, cruise_set_ms=25.0, target_ms=27.0, enabled=True, enable_acc=True, adjust=True, standstill=False, stalk_btn=0):
  cs = SimpleNamespace()
  cs.enableACC = enable_acc
  cs.speed_units = speed_units
  cs.cruise_buttons = stalk_btn
  cs.stock_cruise_set_speed_ms = cruise_set_ms
  cs._tinkla = SimpleNamespace(adjust_acc_with_speed_limit=adjust)
  cs.out = SimpleNamespace(
    vEgo=v_ego_ms,
    cruiseState=SimpleNamespace(enabled=enabled, standstill=standstill, speed=cruise_set_ms),
  )
  cs._calc_speed_limit_target_ms = lambda _: target_ms
  return cs


def test_acc_module_requests_resume_for_positive_offset():
  c = ACCController(press_cooldown_frames=0, human_guard_frames=0, auto_guard_frames=0)
  cs = _build_cs(speed_units="KPH", target_ms=30 * CV.KPH_TO_MS, cruise_set_ms=25 * CV.KPH_TO_MS)

  send, btn = c.update(cs, lat_active=True, frame=100)

  assert send
  assert btn == int(CruiseButtons.RES_ACCEL_2ND)


def test_acc_module_requests_decel_for_negative_offset():
  c = ACCController(press_cooldown_frames=0, human_guard_frames=0, auto_guard_frames=0)
  cs = _build_cs(speed_units="MPH", target_ms=50 * CV.MPH_TO_MS, cruise_set_ms=55 * CV.MPH_TO_MS)

  send, btn = c.update(cs, lat_active=True, frame=100)

  assert send
  assert btn in (int(CruiseButtons.DECEL_SET), int(CruiseButtons.DECEL_2ND))


def test_acc_module_human_guard_blocks_automation():
  c = ACCController(press_cooldown_frames=0, human_guard_frames=300, auto_guard_frames=0)
  cs = _build_cs()

  c.note_human_action(frame=10, cruise_button=int(CruiseButtons.RES_ACCEL), prev_button=int(CruiseButtons.IDLE))
  send, btn = c.update(cs, lat_active=True, frame=200)

  assert not send
  assert btn == int(CruiseButtons.IDLE)
