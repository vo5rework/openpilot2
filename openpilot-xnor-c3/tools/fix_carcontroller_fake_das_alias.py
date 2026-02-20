#!/usr/bin/env python3
"""
/data/openpilot/tools/fix_carcontroller_fake_das_alias.py

Fix card crash:
  NameError: name 'create_fake_das_msg' is not defined. Did you mean: '_create_fake_das'?

Actions:
- Ensure a module-level alias _create_fake_das exists (try/except import)
- Replace can_sends.append(create_fake_das_msg(...)) -> can_sends.append(_create_fake_das(...))
- Replace bare create_fake_das_msg(...) -> _create_fake_das(...)

Targets:
  /data/openpilot/opendbc/car/tesla/carcontroller.py
  /data/openpilot/opendbc_repo/opendbc/car/tesla/carcontroller.py

Backup: *.bak_fake_das_alias
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

ALIAS_SNIPPET = """
try:
  from opendbc.car.tesla.teslacan import create_fake_das_msg as _create_fake_das
except ImportError:
  from opendbc.car.tesla.teslacan import create_fake_das_message as _create_fake_das
""".strip("\n") + "\n"

MARKER = "UNITY_PARITY_FAKE_DAS_ALIAS_FIX"

RE_HAS_ALIAS = re.compile(r"^\s*from\s+opendbc\.car\.tesla\.teslacan\s+import\s+create_fake_das_msg\s+as\s+_create_fake_das\s*$", re.M)
RE_ANY_ALIAS = re.compile(r"^\s*(_create_fake_das)\s*=\s*", re.M)

RE_IMPORTS_BLOCK = re.compile(r"^(?:import .*\n|from .*\n)+", re.M)

RE_APPEND_CALL = re.compile(r"can_sends\.append\(\s*create_fake_das_msg\s*\(", re.M)
RE_BARE_CALL = re.compile(r"(?<!\.)\bcreate_fake_das_msg\s*\(", re.M)

def patch_one(p: Path) -> bool:
  if not p.exists():
    return False

  old = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  if MARKER in old:
    print(f"OK already patched: {p}")
    return True

  new = old

  # 1) Ensure alias exists near imports (only if it's not already defined)
  if not RE_HAS_ALIAS.search(new):
    m = RE_IMPORTS_BLOCK.search(new)
    if m:
      insert_at = m.end()
      new = new[:insert_at] + "\n# " + MARKER + "\n" + ALIAS_SNIPPET + "\n" + new[insert_at:]
    else:
      # fallback: prepend
      new = "# " + MARKER + "\n" + ALIAS_SNIPPET + "\n" + new

  # 2) Rewrite calls to use _create_fake_das
  new = RE_APPEND_CALL.sub("can_sends.append(_create_fake_das(", new)
  new = RE_BARE_CALL.sub("_create_fake_das(", new)

  # Validate syntax
  ast.parse(new, filename=str(p))

  bak = p.with_suffix(p.suffix + ".bak_fake_das_alias")
  if not bak.exists():
    bak.write_text(old, encoding="utf-8")
  p.write_text(new, encoding="utf-8")
  print(f"PATCHED: {p} (backup: {bak})")
  return True

def main() -> int:
  any_done = False
  for p in TARGETS:
    if p.exists():
      patch_one(p)
      any_done = True
  if not any_done:
    raise SystemExit("No Tesla carcontroller.py found in expected locations")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
