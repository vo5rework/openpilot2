# /data/openpilot/panda_diag_irq.py
from panda import Panda
import time

def snap(p):
  h = p.health()
  ch = [p.can_health(b) for b in (0, 1, 2)]
  return h, ch

def d(a, b, k):
  return int(b.get(k, 0)) - int(a.get(k, 0))

serials = sorted(Panda.list())
print("pandas:", serials)

ps = []
for s in serials:
  try:
    ps.append(Panda(s, cli=False))
  except Exception as e:
    print("connect failed", s, e)

a = [(p.get_serial()[0],) + snap(p) for p in ps]
time.sleep(2)
b = [(p.get_serial()[0],) + snap(p) for p in ps]

for (sa, ha, ca), (_, hb, cb) in zip(a, b):
  print("\n==", sa, "==")
  print("safety_mode:", ha.get("safety_mode"), "safety_param:", ha.get("safety_param"))
  print("fault_status:", hb.get("fault_status"), "faults:", hex(hb.get("faults", 0)), "uptime:", hb.get("uptime"))

  for bus in (0, 1, 2):
    aa, bb = ca[bus], cb[bus]
    print(f"bus{bus}: "
          f"rx +{d(aa, bb, 'total_rx_cnt')} "
          f"tx +{d(aa, bb, 'total_tx_cnt')} "
          f"err +{d(aa, bb, 'total_error_cnt')} "
          f"bus_off {bb.get('bus_off')} "
          f"REC {bb.get('receive_error_cnt')} TEC {bb.get('transmit_error_cnt')} "
          f"LEC {bb.get('last_error')} "
          f"irq(rx) {bb.get('irq1_call_rate')} irq(tx) {bb.get('irq0_call_rate')} irq(sce) {bb.get('irq2_call_rate')}")
