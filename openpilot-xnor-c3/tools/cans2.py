from panda import Panda
import time

SERIALS = sorted(Panda.list())
print("pandas:", SERIALS)

# set these to match your real setup
MODE = 36       # tesla_legacy safety mode in your build
PARAMS = {
  SERIALS[0]: 42,  # external panda (example)
  SERIALS[1]: 41,  # main panda (example)
}

ps = {}
for s in SERIALS:
  p = Panda(s, cli=False)
  ps[s] = p
  p.set_safety_mode(MODE, PARAMS[s])
  print("set", s, "mode", MODE, "param", PARAMS[s])

time.sleep(0.5)

def snap(p):
  h = p.health()
  ch = [p.can_health(b) for b in (0,1,2)]
  return h, ch

def d(a,b,k): return int(b.get(k,0))-int(a.get(k,0))

a = {s: snap(ps[s]) for s in SERIALS}
time.sleep(2)
b = {s: snap(ps[s]) for s in SERIALS}

for s in SERIALS:
  ha, ca = a[s]
  hb, cb = b[s]
  print("\n==", s, "==")
  print("safety_mode:", hb.get("safety_mode"), "safety_param:", hb.get("safety_param"))
  print("fault_status:", hb.get("fault_status"), "faults:", hex(hb.get("faults",0)), "uptime:", hb.get("uptime"))
  for bus in (0,1,2):
    aa, bb = ca[bus], cb[bus]
    print(f"bus{bus}: rx +{d(aa,bb,'total_rx_cnt')} tx +{d(aa,bb,'total_tx_cnt')} err +{d(aa,bb,'total_error_cnt')} "
          f"REC {bb.get('receive_error_cnt')} TEC {bb.get('transmit_error_cnt')} LEC {bb.get('last_error')} "
          f"irq(rx) {bb.get('irq1_call_rate')} irq(tx) {bb.get('irq0_call_rate')} irq(sce) {bb.get('irq2_call_rate')}")

