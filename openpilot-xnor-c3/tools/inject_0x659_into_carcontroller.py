#!/usr/bin/env python3
"""
Inject Unity-style internal 0x659 carrier into Tesla CarController update path.

Why:
- Your logs show sendcan_buses=[] even with external daemon.
- sendcan is effectively single-producer; publish inside the existing control loop.

What it does:
- Adds self._op659_prev_btn in CarController.__init__
- Inserts a 0x659 append block *before every* 'return ... can_sends ...' inside update()
- Uses existing _create_fake_das import if present; otherwise adds a tolerant import.

Targets:
- /data/openpilot/opendbc/car/tesla/carcontroller.py
- /data/openpilot/opendbc_repo/opendbc/car/tesla/carcontroller.py (if exists)

Backups:
- *.bak_inject659
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path("/data/openpilot")
TARGETS = [
  ROOT / "opendbc/car/tesla/carcontroller.py",
  ROOT / "opendbc_repo/opendbc/car/tesla/carcontroller.py",
]

DEF_UPDATE_RE = re.compile(r"^(\s*)def\s+update\s*\(.*\)\s*:\s*$", re.M)
DEF_NEXT_AT_SAME_INDENT_RE = lambda ind: re.compile(rf"^{re.escape(ind)}def\s+\w+\s*\(", re.M)
RETURN_CANSENDS_RE = re.compile(r"^(?P<ind>\s+)return\b[^\n]*\bcan_sends\b[^\n]*$", re.M)

INIT_RE = re.compile(r"^(\s*)def\s+__init__\s*\(self,.*\)\s*:\s*$", re.M)
SUPER_INIT_RE = re.compile(r"^\s*super\(\)\.__init__\(", re.M)

IMPORT_TOLERANT = """\
try:
  from opendbc.car.tesla.teslacan import create_fake_das_msg as _create_fake_das
except ImportError:
  from opendbc.car.tesla.teslacan import create_fake_das_message as _create_fake_das
"""

INJECT_MARKER = "# OP_INTERNAL_0x659_CARRIER"

def _norm(s: str) -> str:
  return s.replace("\r\n", "\n").replace("\r", "\n")

def _backup_write(p: Path, old: str, new: str) -> None:
  if new == old:
    return
  bak = p.with_suffix(p.suffix + ".bak_inject659")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")

def _ensure_tolerant_import(src: str) -> str:
  if "_create_fake_das" in src and "except ImportError" in src:
    return src
  # insert after initial imports
  lines = src.splitlines(True)
  ins = 0
  for i, line in enumerate(lines):
    if line.startswith("import ") or line.startswith("from "):
      ins = i + 1
    elif i > 60:
      break
  lines.insert(ins, IMPORT_TOLERANT + "\n")
  return "".join(lines)

def _ensure_prev_btn_in_init(src: str) -> str:
  # locate CarController.__init__ and add self._op659_prev_btn = 0 after super().__init__
  m_init = INIT_RE.search(src)
  if not m_init:
    return src
  ind = m_init.group(1)
  after = src[m_init.end():]
  m_next = re.search(rf"^{re.escape(ind)}def\s+\w+\s*\(", after, re.M)
  end = m_init.end() + (m_next.start() if m_next else len(after))
  block = src[m_init.start():end]

  if "self._op659_prev_btn" in block:
    return src

  lines = block.splitlines(True)
  for i, ln in enumerate(lines):
    if SUPER_INIT_RE.search(ln):
      # insert next line with same indent level as super() line
      super_indent = re.match(r"^(\s*)", ln).group(1)
      lines.insert(i + 1, f"{super_indent}self._op659_prev_btn = 0\n")
      new_block = "".join(lines)
      return src[:m_init.start()] + new_block + src[end:]
  return src

def _inject_before_returns_in_update(src: str) -> str:
  m_up = DEF_UPDATE_RE.search(src)
  if not m_up:
    raise RuntimeError("couldn't find def update(...)")
  ind = m_up.group(1)
  after = src[m_up.end():]
  m_next = DEF_NEXT_AT_SAME_INDENT_RE(ind).search(after)
  end = m_up.end() + (m_next.start() if m_next else len(after))

  block = src[m_up.start():end]
  if INJECT_MARKER in block:
    return src  # already injected

  returns = list(RETURN_CANSENDS_RE.finditer(block))
  if not returns:
    rets = [ln for ln in block.splitlines() if ln.strip().startswith("return")][:20]
    raise RuntimeError("no 'return ... can_sends ...' found in update(); returns seen:\n" + "\n".join(rets))

  # injection code uses the return indentation level (same scope as can_sends)
  def make_inject(ret_indent: str) -> str:
    return (
      f"\n{ret_indent}{INJECT_MARKER}\n"
      f"{ret_indent}stalk_btn = int(getattr(CS, 'cruise_buttons', 0))\n"
      f"{ret_indent}prev_btn = int(getattr(self, '_op659_prev_btn', 0))\n"
      f"{ret_indent}main_edge = (stalk_btn == 2) and (prev_btn != 2)\n"
      f"{ret_indent}cancel_edge = (stalk_btn == 1) and (prev_btn != 1)\n"
      f"{ret_indent}self._op659_prev_btn = stalk_btn\n"
      f"{ret_indent}ap_disabled = Params().get_bool('TinklaAutopilotDisabled')\n"
      f"{ret_indent}pedal_en = Params().get_bool('TinklaPedalEnabled')\n"
      f"{ret_indent}if ((self.frame % 10) == 0) or main_edge or cancel_edge:\n"
      f"{ret_indent}  for bus in (CANBUS.party, CANBUS.party + 4):\n"
      f"{ret_indent}    can_sends.append(_create_fake_das(pedal_en, ap_disabled,\n"
      f"{ret_indent}                                   stalk_main=main_edge,\n"
      f"{ret_indent}                                   stalk_cancel=cancel_edge,\n"
      f"{ret_indent}                                   bus=bus))\n"
    )

  # Insert before EVERY return that includes can_sends (covers early returns)
  out = []
  last = 0
  for r in returns:
    ret_ind = r.group("ind")
    out.append(block[last:r.start()])
    out.append(make_inject(ret_ind))
    out.append(block[r.start():r.end()] + "\n")
    last = r.end() + 1  # consume newline
  out.append(block[last:])

  new_block = "".join(out)
  return src[:m_up.start()] + new_block + src[end:]

def patch_file(p: Path) -> None:
  if not p.exists():
    return
  old = _norm(p.read_text(encoding="utf-8", errors="replace"))
  new = old
  new = _ensure_tolerant_import(new)
  new = _ensure_prev_btn_in_init(new)
  new = _inject_before_returns_in_update(new)

  ast.parse(new, filename=str(p))
  _backup_write(p, old, new)
  print(f"PATCHED: {p}")

def main() -> int:
  patched_any = False
  for p in TARGETS:
    if p.exists():
      patch_file(p)
      patched_any = True
  if not patched_any:
    raise SystemExit("no target carcontroller.py files found")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
