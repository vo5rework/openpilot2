#!/usr/bin/env python3
"""
Insert a pre-validity STW_ACTN_RQ (0x45) latch into tesla_legacy_rx_hook.

Why:
- Some dual-panda topologies see 0x45 in logs, but rx_hook code that is inside
  `if (valid) { ... }` never executes on one panda due to rx validity gating.
- This causes controlsAllowed mismatch (one True, one False).

What it does:
- Locates tesla_legacy_rx_hook() with a signature-agnostic regex
- Injects a small block immediately after the opening brace that:
    - if addr==0x45 and stalk-contract enabled => pcm_cruise_check(true/false)
- Removes illegal file-scope assignments for op flags (recurring build issue)
- Creates a .bak backup
- Idempotent (won't inject twice)

Usage:
  sudo python3 /data/openpilot/patch_prevalid_stalk_latch_v2.py \
    /data/openpilot/opendbc_repo/opendbc/safety/modes/tesla_legacy.h
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


INJECT_MARK = "/* OP_STALK_PREVALID_LATCH */"

# Match function header with flexible return type + flexible const + flexible arg name/spacing
RX_HOOK_RE = re.compile(
  r"(static\s+[^{;\n]*\btesla_legacy_rx_hook\s*"
  r"\(\s*(?:const\s+)?CANPacket_t\s*\*\s*([A-Za-z_]\w*)\s*\)\s*\{\s*\n)",
  re.M,
)

ILLEGAL_ASSIGN_RE = re.compile(
  r"^\s*(tesla_legacy_op_autopilot_disabled|tesla_legacy_op_pedal_enabled)\s*=\s*false\s*;\s*$",
  re.M,
)


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("path", type=Path)
  args = ap.parse_args()

  p: Path = args.path
  s = p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

  # Hard guard: delete recurring illegal file-scope assignments
  s = ILLEGAL_ASSIGN_RE.sub("", s)

  if INJECT_MARK in s:
    print("OK: prevalid latch already present")
    return 0

  m = RX_HOOK_RE.search(s)
  if not m:
    # Print a hint for manual debugging
    hint = []
    for i, line in enumerate(s.splitlines(), 1):
      if "tesla_legacy_rx_hook" in line:
        hint.append(f"{i}: {line}")
    raise SystemExit("ERROR: couldn't find tesla_legacy_rx_hook(). Candidates:\n" + "\n".join(hint[:10]))

  arg = m.group(2)
  inject = (
    f"  {INJECT_MARK}\n"
    "  // Unity parity: stalk->controlsAllowed latch must not depend on addr_safety_check validity.\n"
    "  // Dual-panda configs can see 0x45 but fail rx validity gating on one panda.\n"
    "  const bool _has_ap_hw = tesla_hw1 || tesla_hw2 || tesla_hw3;\n"
    "  const bool _use_stalk = tesla_legacy_op_stalk_enable || (!_has_ap_hw) || tesla_legacy_op_autopilot_disabled;\n"
    f"  if (({arg}->addr == 0x45U) && _use_stalk) {{\n"
    f"    const int ap_lever_position = (int)(GET_BYTES({arg}, 0U, 1U) & 0x3FU);\n"
    "    if (ap_lever_position == 2) {\n"
    "      pcm_cruise_check(true);\n"
    "    } else if (ap_lever_position == 1) {\n"
    "      pcm_cruise_check(false);\n"
    "    }\n"
    "  }\n\n"
  )

  out = s[:m.end(1)] + inject + s[m.end(1):]

  # Final guard: ensure illegal assignments are gone
  if ILLEGAL_ASSIGN_RE.search(out):
    raise SystemExit("ERROR: illegal file-scope assignment still present after patch")

  bak = p.with_suffix(p.suffix + ".bak_prevalid_v2")
  bak.write_text(s, encoding="utf-8")
  p.write_text(out, encoding="utf-8")
  print(f"FIXED: inserted prevalid stalk latch (backup: {bak})")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
