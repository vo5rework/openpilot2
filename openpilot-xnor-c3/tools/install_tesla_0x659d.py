#!/usr/bin/env python3
"""
/data/openpilot/tools/install_tesla_0x659d.py

Installs a tiny daemon that continuously publishes Tesla fake DAS msg 0x659 to BOTH panda bus blocks
(buses 0 and 4) so teslaLegacy safety can latch stalk->controlsAllowed on both pandas (Unity parity).

- Writes:  /data/openpilot/selfdrive/tesla_0x659d.py
- Patches: /data/openpilot/system/manager/process_config.py (inserts PythonProcess entry once)
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path("/data/openpilot")
DAEMON_PATH = ROOT / "selfdrive" / "tesla_0x659d.py"
PROC_CFG_PATH = ROOT / "system" / "manager" / "process_config.py"


DAEMON_CONTENT = r'''#!/usr/bin/env python3
"""
selfdrive/tesla_0x659d.py

Unity parity: publish FAKE DAS / panda-internal message 0x659 continuously so teslaLegacy safety
can read:
  - autopilot_disabled bit (byte5 bit7)
  - pedal_enabled bit (byte5 bit5)

Safety consumes this in tx_hook and blocks it from the car; it's only a mode-carrier.

This daemon publishes 0x659 to both panda "bus blocks" in dual-panda setups:
  - bus 0 (panda0 block)
  - bus 4 (panda1 block)

It only publishes when teslaLegacy safety is active (safetyParam != 0).
"""

from __future__ import annotations

import time
from typing import Optional

from cereal import messaging, car
from openpilot.common.params import Params

ADDR = 0x659
PUB_HZ = 10.0
BUS_BLOCKS = (0, 4)

# Prefer canonical param keys, but tolerate older/typo variants
AP_DISABLED_KEYS = ("TinklaAutopilotDisabled", "Tinklaautopilotdisabled")
PEDAL_ENABLED_KEYS = ("TinklaPedalEnabled", "TinklaPedalenabled")


def _read_bool(params: Params, keys: tuple[str, ...], default: bool = False) -> bool:
  for k in keys:
    v = params.get(k)
    if v is None:
      continue
    try:
      s = v.decode("utf-8", errors="ignore").strip().lower()
    except Exception:
      continue
    if s in ("1", "true", "yes", "y", "on"):
      return True
    if s in ("0", "false", "no", "n", "off"):
      return False
  return default


def _tesla_legacy_active(sm: messaging.SubMaster) -> bool:
  if "pandaStates" not in sm.data:
    return False
  pss = sm["pandaStates"]
  if pss is None:
    return False
  for ps in pss:
    if ps.safetyModel == car.CarParams.SafetyModel.teslaLegacy and ps.safetyParam != 0:
      return True
  return False


def _make_0x659(ap_disabled: bool, pedal_enabled: bool) -> bytes:
  # Unity format: byte5:
  #   bits0-4 legal_speed_limit (unused here -> 0)
  #   bit5   pedalEnabled
  #   bit7   autopilot_disabled
  b5 = (0x80 if ap_disabled else 0x00) | (0x20 if pedal_enabled else 0x00)
  return bytes([0, 0, 0, 0, 0, b5, 0, 0])


def main() -> int:
  params = Params()
  pm = messaging.PubMaster(["sendcan"])
  sm = messaging.SubMaster(["pandaStates"])

  last_print = 0.0

  while True:
    sm.update(1000)  # 1s timeout

    active = _tesla_legacy_active(sm)
    ap_disabled = _read_bool(params, AP_DISABLED_KEYS, default=False)
    pedal_enabled = _read_bool(params, PEDAL_ENABLED_KEYS, default=False)

    now = time.time()
    if now - last_print > 1.0:
      print(f"[tesla_0x659d] active={active} ap_disabled={ap_disabled} pedal_enabled={pedal_enabled}")
      last_print = now

    if not active:
      time.sleep(0.2)
      continue

    dat = _make_0x659(ap_disabled=ap_disabled, pedal_enabled=pedal_enabled)
    msg = messaging.new_message("sendcan", size=len(BUS_BLOCKS))

    for i, bus in enumerate(BUS_BLOCKS):
      msg.sendcan[i].address = ADDR
      msg.sendcan[i].dat = dat
      msg.sendcan[i].src = bus

    pm.send("sendcan", msg)
    time.sleep(1.0 / PUB_HZ)


if __name__ == "__main__":
  raise SystemExit(main())
'''


def _backup(path: Path) -> None:
  bak = path.with_suffix(path.suffix + ".bak_tesla0x659d")
  if not bak.exists():
    bak.write_bytes(path.read_bytes())


def _write_daemon() -> None:
  DAEMON_PATH.parent.mkdir(parents=True, exist_ok=True)
  if DAEMON_PATH.exists():
    # Only overwrite if it doesn't already contain our marker
    if "selfdrive/tesla_0x659d.py" in DAEMON_PATH.read_text(errors="ignore"):
      print(f"OK: daemon already present: {DAEMON_PATH}")
      return
    _backup(DAEMON_PATH)
  DAEMON_PATH.write_text(DAEMON_CONTENT, encoding="utf-8")
  os.chmod(DAEMON_PATH, 0o755)
  print(f"OK: wrote daemon: {DAEMON_PATH}")


def _patch_process_config() -> None:
  if not PROC_CFG_PATH.exists():
    raise RuntimeError(f"Missing: {PROC_CFG_PATH}")

  txt = PROC_CFG_PATH.read_text(encoding="utf-8", errors="ignore")
  if 'PythonProcess("tesla_0x659d"' in txt:
    print(f"OK: process already registered in {PROC_CFG_PATH}")
    return

  _backup(PROC_CFG_PATH)

  marker = "  # debug procs\n"
  ins = '  PythonProcess("tesla_0x659d", "selfdrive.tesla_0x659d", only_onroad),\n\n'
  if marker not in txt:
    raise RuntimeError("Couldn't find insertion marker '# debug procs' in process_config.py")

  txt2 = txt.replace(marker, ins + marker)
  PROC_CFG_PATH.write_text(txt2, encoding="utf-8")
  print(f"OK: patched manager config: {PROC_CFG_PATH}")


def main() -> int:
  _write_daemon()
  _patch_process_config()
  print("DONE. Reboot recommended (or restart manager).")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
