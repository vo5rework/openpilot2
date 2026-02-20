#!/usr/bin/env python3
"""
/data/openpilot/tools/patch_carcontroller_0x659_from_msg_stw.py

Adds robust stalk MAIN/CANCEL detection using CS.msg_stw_actn_req["SpdCtrlLvr_Stat"]
and passes stalk_main/stalk_cancel into create_fake_das_msg(...) or _create_fake_das(...).

Targets:
  /data/openpilot/opendbc/car/tesla/carcontroller.py
  /data/openpilot/opendbc_repo/opendbc/car/tesla/carcontroller.py

Backup: *.bak_0x659_from_stw
"""
from __future__ import annotations
import re
from pathlib import Path

TARGETS = [
  Path("/data/openpilot/opendbc/car/tesla/carcontroller.py"),
  Path("/data/openpilot/opendbc_repo/opendbc/car/tesla/carcontroller.py"),
]

MARK = "UNITY_PARITY_0x659_FROM_MSG_STW"

UPDATE_DEF_RE = re.compile(r'(?m)^(?P<ind>[ \t]*)def\s+update\s*\(.*\)\s*:\s*$')
CAN_SENDS_RE = re.compile(r'(?m)^(?P<ind>[ \t]*)can_sends\s*=\s*\[\]\s*$')

def add_stalk_block(src: str) -> str:
  if MARK in src:
    return src
  m_up = UPDATE_DEF_RE.search(src)
  if not m_up:
    raise RuntimeError("carcontroller.py: couldn't find def update(...)")
  # insert after first "can_sends = []" inside update
  after = src[m_up.end():]
  m_cs = CAN_SENDS_RE.search(after)
  if not m_cs:
    raise RuntimeError("carcontroller.py: couldn't find 'can_sends = []' inside update()")
  ins_at = m_up.end() + m_cs.end()
  ind = m_cs.group("ind")
  block = (
    f"\n{ind}# {MARK}\n"
    f"{ind}stw = getattr(CS, 'msg_stw_actn_req', None) or {{}}\n"
    f"{ind}lever = int(stw.get('SpdCtrlLvr_Stat', getattr(CS, 'cruise_buttons', 0) or 0))\n"
    f"{ind}stalk_main = (lever == 2)\n"
    f"{ind}stalk_cancel = (lever == 1)\n"
  )
  return src[:ins_at] + block + src[ins_at:]


def inject_named_args(src: str, func_name: str) -> str:
  """
  Adds ', stalk_main=stalk_main, stalk_cancel=stalk_cancel' to calls of func_name(...)
  if not already present. Works across multiline parentheses by scanning.
  """
  out = []
  i = 0
  while True:
    j = src.find(func_name + "(", i)
    if j < 0:
      out.append(src[i:])
      break
    out.append(src[i:j])
    k = j + len(func_name) + 1  # index after '('
    depth = 1
    while k < len(src) and depth > 0:
      c = src[k]
      if c == "(":
        depth += 1
      elif c == ")":
        depth -= 1
      k += 1
    call = src[j:k]  # includes closing ')'
    if "stalk_main" in call or "stalk_cancel" in call:
      out.append(call)
    else:
      # insert before last ')'
      out.append(call[:-1] + ", stalk_main=stalk_main, stalk_cancel=stalk_cancel)")
    i = k
  return "".join(out)


def patch_file(p: Path) -> None:
  s = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  s2 = add_stalk_block(s)

  # Patch common call sites
  for fn in ("create_fake_das_msg", "_create_fake_das"):
    s2 = inject_named_args(s2, fn)

  if s2 == s:
    print(f"OK: no change needed: {p}")
    return

  bak = p.with_suffix(p.suffix + ".bak_0x659_from_stw")
  if not bak.exists():
    bak.write_text(s, encoding="utf-8")
  p.write_text(s2, encoding="utf-8")
  print(f"PATCHED: {p} (backup: {bak})")


def main() -> int:
  any_ = False
  for p in TARGETS:
    if p.exists():
      patch_file(p); any_ = True
  if not any_:
    raise SystemExit("No tesla carcontroller.py found")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
