#!/usr/bin/env python3
"""
/data/openpilot/tools/fix_tesla_carstate_cruise_buttons_v2.py

Populate Tesla CarState internal attribute `self.cruise_buttons` from stalk message:
  cp.vl["STW_ACTN_RQ"]["SpdCtrlLvr_Stat"]

Works across forks where the method isn't named `update()` by inserting just before
the last `return ret` in the file (usually the function that returns CarState msg).

Patches both trees if present:
  /data/openpilot/opendbc/car/tesla/carstate.py
  /data/openpilot/opendbc_repo/opendbc/car/tesla/carstate.py

Backup: *.bak_cruise_buttons_v2
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path("/data/openpilot")
TARGETS = [
  ROOT / "opendbc/car/tesla/carstate.py",
  ROOT / "opendbc_repo/opendbc/car/tesla/carstate.py",
]

MARKER = "UNITY_PARITY_CRUISE_BUTTONS_FROM_STALK_V2"

DEF_INIT_RE = re.compile(r"^(?P<ind>\s*)def\s+__init__\s*\(\s*self\b.*\)\s*:\s*$", re.M)
RETURN_RET_RE = re.compile(r"^(?P<ind>\s*)return\s+ret\s*$", re.M)

INIT_SNIPPET = f"""# {MARKER}
self.cruise_buttons = 0
"""

# supports forks where message/signal names differ slightly
UPDATE_SNIPPET = f"""# {MARKER}
btn = None
for msg_name in ("STW_ACTN_RQ", "STW_ACTN_REQ", "STW_ACTN"):
  if msg_name in cp.vl:
    vl = cp.vl[msg_name]
    for sig in ("SpdCtrlLvr_Stat", "SpdCtrlLvr_StatRaw", "SpdCtrlLvrStat"):
      if sig in vl:
        btn = int(vl[sig])
        break
    if btn is not None:
      break
if btn is not None:
  self.cruise_buttons = btn
"""

def _norm(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")

def _insert_in_init(src: str) -> str:
  if MARKER in src or "self.cruise_buttons" in src:
    return src

  m = DEF_INIT_RE.search(src)
  if not m:
    return src

  # Insert after the def line (next line indentation is assumed to be ind + 2 spaces or 4 spaces)
  start = m.end()
  # determine body indent by looking at the next non-empty line
  tail = src[start:].splitlines(True)
  body_ind = (m.group("ind") + "  ")
  for ln in tail[:20]:
    if ln.strip():
      body_ind = re.match(r"^(\s*)", ln).group(1)
      break

  ins = "\n" + "\n".join(body_ind + l for l in INIT_SNIPPET.strip("\n").splitlines()) + "\n"
  return src[:start] + ins + src[start:]

def _insert_before_last_return_ret(src: str) -> str:
  if MARKER in src:
    return src

  matches = list(RETURN_RET_RE.finditer(src))
  if not matches:
    raise RuntimeError("couldn't find any 'return ret' in file")

  m = matches[-1]
  ind = m.group("ind")
  ins = "\n" + "\n".join(ind + l for l in UPDATE_SNIPPET.strip("\n").splitlines()) + "\n"
  return src[:m.start()] + ins + src[m.start():]

def patch_file(p: Path) -> None:
  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  if MARKER in old:
    print(f"OK already patched: {p}")
    return

  new = old
  new = _insert_in_init(new)
  new = _insert_before_last_return_ret(new)

  ast.parse(new, filename=str(p))

  bak = p.with_suffix(p.suffix + ".bak_cruise_buttons_v2")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")
  print(f"PATCHED: {p} (backup: {bak})")

def main() -> int:
  any_found = False
  for p in TARGETS:
    if p.exists():
      any_found = True
      patch_file(p)
  if not any_found:
    raise SystemExit("No tesla carstate.py found in expected locations")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
