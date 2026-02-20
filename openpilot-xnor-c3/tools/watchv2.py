from openpilot.common.params import Params
from cereal import car, messaging

p = Params()
raw = p.get("CarParams")
print("CarParams present:", bool(raw))
if raw:
  cp = car.CarParams.from_bytes(raw)
  print("carName:", cp.carName)
  print("carFingerprint:", cp.carFingerprint)
  print("safetyConfigs:", [(sc.safetyModel, sc.safetyParam) for sc in cp.safetyConfigs])

sm = messaging.SubMaster(["pandaStates"], ignore_avg_freq=True)
sm.update(1000)
for i, ps in enumerate(sm["pandaStates"]):
  print(f"panda[{i}] safetyModel={ps.safetyModel} safetyParam={ps.safetyParam} controlsAllowed={ps.controlsAllowed} faultStatus={ps.faultStatus}")
