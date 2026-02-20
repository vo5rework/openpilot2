#!/usr/bin/env python3
"""
/data/openpilot/tools/sanitize_card_0x659.py

Cleans up Tesla 0x659 carrier wiring in card.py:

- Removes duplicate imports:
    from selfdrive.tesla_0x659 import Tesla659Carrier
  Keeps:
    from openpilot.selfdrive.tesla_0x659 import Tesla659Carrier

- Removes conflicting UNITY_PARITY_* blocks for 0x659 (V2/V3 fragments).
- Ensures exactly one carrier init in __init__ after Ratekeeper creation:
    self._tesla_659 = Tesla659Carrier() if getattr(self.CP, "carName", "") == "tesla" else None

- Ensures exactly one publish block in state_update immediately after can_list creation:
    extra = self._tesla_659.tick(can_list)
    send to sendcan via existing publisher

Backup: card.py.bak_sanitize_0x659
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

CARD = Path("/data/openpilot/selfdrive/car/card.py")
BAK = CARD.with_suffix(".py.bak_sanitize_0x659")

IMPORT_KEEP = "from openpilot.selfdrive.tesla_0x659 import Tesla659Carrier"
IMPORT_DROP_RE = re.compile(r"(?m)^\s*from\s+selfdrive\.tesla_0x659\s+import\s+Tesla659Carrier\s*\n")
IMPORT_KEEP_DUP_RE = re.compile(r"(?m)^\s*from\s+openpilot\.selfdrive\.tesla_0x659\s+import\s+Tesla659Carrier\s*\n")

# Drop any lines with these markers (we reinsert one clean wiring)
DROP_MARKER_LINES_RE = re.compile(r"(?m)^\s*#\s*UNITY_PARITY_0x659.*\n")

# Drop old helper state
DROP_TESLA659_FRAME_RE = re.compile(r"(?m)^\s*self\._tesla_659_frame\s*=\s*\d+\s*\n")

# Drop any old tesla_659 assignments (we'll insert one clean)
DROP_TESLA659_ASSIGN_RE = re.compile(r"(?m)^\s*self\._tesla_659\s*=\s*Tesla659Carrier\([^\n]*\)\s*\n")

# Drop old publish blocks that reference _tesla_659_frame, update_from_can, build_msgs, tick with 2 args, etc.
DROP_STATEUPDATE_BLOCK_RE = re.compile(
  r"(?ms)^\s*#\s*UNITY_PARITY_0x659.*?^\s*(?=CS\s*=\s*self\.CI\.update|#\s*Update carState|CS\s*=)",
)

DROP_UPDATE_FROM_CAN_RE = re.compile(r"(?ms)^\s*#\s*UNITY_PARITY_0x659_FROM_CARD_V3: edge-detect.*?\n(?=^\s*(if self\.CP\.brand|# Update radar|RD\s*=))")

DROP_STATEPUBLISH_BLOCK_RE = re.compile(
  r"(?ms)^\s*#\s*UNITY_PARITY_0x659_FROM_CARD_V3: publish 0x659.*?\n(?=^\s*(if RD is not None:|if RD is not None|# kick off controlsd|cs_send =))"
)

RATEKEEPER_RE = re.compile(r"(?m)^\s*self\.rk\s*=\s*Ratekeeper\([^\n]*\)\s*$")
CANLIST_RE = re.compile(r"(?m)^\s*can_list\s*=\s*can_capnp_to_list\(can_strs\)\s*$")

def main() -> int:
  if not CARD.exists():
    raise SystemExit(f"missing: {CARD}")

  s = CARD.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
  orig = s

  # Imports: drop selfdrive import, ensure single openpilot import
  s = IMPORT_DROP_RE.sub("", s)
  # Remove all existing keep imports and reinsert exactly once near the import area
  s = IMPORT_KEEP_DUP_RE.sub("", s)

  # Insert keep import after other imports (best-effort: after last import/from at file top)
  m_import_area = re.search(r"(?m)\A(?:[^\n]*\n)*?(?=(?:\nclass\s+|def\s+|REPLAY\s*=))", s)
  if not m_import_area:
    raise RuntimeError("couldn't locate import area to insert Tesla659Carrier import")
  import_area = s[:m_import_area.end()]
  rest = s[m_import_area.end():]
  if IMPORT_KEEP not in import_area:
    # put it near other openpilot.selfdrive imports; append safely at end of import area
    import_area = import_area.rstrip("\n") + "\n" + IMPORT_KEEP + "\n\n"
  s = import_area + rest

  # Remove legacy marker lines & old init/publish fragments
  s = DROP_MARKER_LINES_RE.sub("", s)
  s = DROP_TESLA659_FRAME_RE.sub("", s)
  s = DROP_TESLA659_ASSIGN_RE.sub("", s)
  s = DROP_STATEPUBLISH_BLOCK_RE.sub("", s)
  s = DROP_UPDATE_FROM_CAN_RE.sub("", s)

  # state_update: remove any marker blocks we failed to delete
  # (soft cleanup: delete any block that calls tick with 2 args)
  s = re.sub(r"(?ms)^\s*if\s+getattr\(self,\s*'_tesla_659'.*?tick\(\s*self\._tesla_659_frame\s*,.*?\)\s*\n\s*if\s+extra:\s*\n\s*self\.pm\.sock\['sendcan'\]\.send.*?\n", "", s)

  # Insert clean init after Ratekeeper
  m_rk = RATEKEEPER_RE.search(s)
  if not m_rk:
    raise RuntimeError("couldn't find Ratekeeper initialization to anchor tesla_659 init")
  rk_line = m_rk.group(0)
  indent = re.match(r"^(\s*)", rk_line).group(1)
  init_block = (
    f"{rk_line}\n\n"
    f"{indent}# TESLA_0x659_CARRIER: single source of truth\n"
    f"{indent}self._tesla_659 = Tesla659Carrier() if getattr(self.CP, 'carName', '') == 'tesla' else None\n"
  )
  s = s[:m_rk.start()] + init_block + s[m_rk.end():]

  # Insert clean publish right after can_list creation in state_update
  m_canlist = CANLIST_RE.search(s)
  if not m_canlist:
    raise RuntimeError("couldn't find can_list = can_capnp_to_list(can_strs) anchor")
  canlist_line = m_canlist.group(0)
  indent2 = re.match(r"^(\s*)", canlist_line).group(1)
  pub_block = (
    f"{canlist_line}\n\n"
    f"{indent2}# TESLA_0x659_CARRIER: publish via existing sendcan publisher\n"
    f"{indent2}if getattr(self, '_tesla_659', None) is not None:\n"
    f"{indent2}  extra = self._tesla_659.tick(can_list)\n"
    f"{indent2}  if extra:\n"
    f"{indent2}    self.pm.sock['sendcan'].send(can_list_to_can_capnp(extra, msgtype='sendcan'))\n"
  )
  s = s[:m_canlist.start()] + pub_block + s[m_canlist.end():]

  # Final parse check
  ast.parse(s, filename=str(CARD))

  if BAK.exists() is False:
    BAK.write_text(orig, encoding="utf-8")
  CARD.write_text(s, encoding="utf-8")
  print(f"PATCHED: {CARD}")
  print(f"BACKUP:  {BAK}")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
