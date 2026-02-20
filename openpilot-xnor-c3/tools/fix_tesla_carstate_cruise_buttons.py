#!/usr/bin/env python3
"""
/data/openpilot/tools/fix_tesla_carstate_cruise_buttons.py

Populate internal CarState attribute `self.cruise_buttons` from STW_ACTN_RQ (0x45)
so CarController can set MAIN/CANCEL bits in 0x659.

Patches both trees if present:
  /data/openpilot/opendbc/car/tesla/carstate.py
  /data/openpilot/opendbc_repo/opendbc/car/tesla/carstate.py

Backups: *.bak_cruise_buttons
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

MARKER = "UNITY_PARITY_CRUISE_BUTTONS_FROM_STALK"

DEF_INIT_RE = re.compile(r"^(?P<ind>\s*)def\s+__init__\s*\(self[^\)]*\)\s*:\s*$", re.M)
DEF_UPDATE_RE = re.compile(r"^(?P<ind>\s*)def\s+update\s*\(self[^\)]*\)\s*:\s*$", re.M)

SUPER_INIT_RE = re.compile(r"^(?P<ind>\s*)super\(\)\.__init__\([^\)]*\)\s*$", re.M)
RETURN_RET_RE = re.compile(r"^(?P<ind>\s*)return\s+ret\s*$", re.M)

INIT_INSERT = f"""# {MARKER}
self.cruise_buttons = 0
"""

UPDATE_INSERT = f"""# {MARKER}
try:
  self.cruise_buttons = int(cp.vl["STW_ACTN_RQ"]["SpdCtrlLvr_Stat"])
except Exception:
  self.cruise_buttons = int(getattr(self, "cruise_buttons", 0))
"""

def _norm(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")

def _block_span(src: str, def_match: re.Match) -> tuple[int, int]:
  start = def_match.start()
  ind = def_match.group("ind")
  after = src[def_match.end():]
  m_next = re.search(rf"^{re.escape(ind)}def\s+\w+\s*\(", after, flags=re.M)
  end = def_match.end() + (m_next.start() if m_next else len(after))
  return start, end

def _insert_after_super_in_init(block: str) -> str:
  if MARKER in block or "self.cruise_buttons" in block:
    return block
  m = SUPER_INIT_RE.search(block)
  if not m:
    # fallback: insert after def line
    lines = block.splitlines(True)
    for i, ln in enumerate(lines):
      if ln.lstrip().startswith("def __init__"):
        indent = re.match(r"^(\s*)", lines[i+1] if i+1 < len(lines) else "    ").group(1)
        ins = "\n" + "\n".join(indent + l for l in INIT_INSERT.strip("\n").splitlines()) + "\n"
        lines.insert(i+1, ins)
        return "".join(lines)
    return block
  ind = m.group("ind")
  ins = "\n" + "\n".join(ind + l for l in INIT_INSERT.strip("\n").splitlines()) + "\n"
  return block[:m.end()] + ins + block[m.end():]

def _insert_before_return_ret_in_update(block: str) -> str:
  if MARKER in block:
    return block
  rets = list(RETURN_RET_RE.finditer(block))
  if not rets:
    return block
  m = rets[-1]
  ind = m.group("ind")
  ins = "\n" + "\n".join(ind + l for l in UPDATE_INSERT.strip("\n").splitlines()) + "\n"
  return block[:m.start()] + ins + block[m.start():]

def patch_file(p: Path) -> None:
  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  if MARKER in old:
    print(f"OK already patched: {p}")
    return

  new = old

  # Patch __init__
  m_init = DEF_INIT_RE.search(new)
  if m_init:
    s, e = _block_span(new, m_init)
    block = new[s:e]
    block2 = _insert_after_super_in_init(block)
    new = new[:s] + block2 + new[e:]

  # Patch update
  m_up = DEF_UPDATE_RE.search(new)
  if not m_up:
    raise RuntimeError(f"{p}: couldn't find def update(...)")
  s, e = _block_span(new, m_up)
  block = new[s:e]
  block2 = _insert_before_return_ret_in_update(block)
  new = new[:s] + block2 + new[e:]

  ast.parse(new, filename=str(p))

  bak = p.with_suffix(p.suffix + ".bak_cruise_buttons")
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
