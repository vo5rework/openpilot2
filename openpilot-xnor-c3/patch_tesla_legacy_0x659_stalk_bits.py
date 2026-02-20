#!/usr/bin/env python3
"""Patch tesla_legacy.h to latch cruise based on 0x659 stalk edge bits.

Adds to tesla_legacy_tx_hook():
  - parse byte5 bits1/0 for MAIN/CANCEL edge
  - call pcm_cruise_check(true/false)
  - return false (never forwarded)

Also ensures 0x659 exists in tesla_legacy_tx_msgs allowlist.

Usage:
  sudo python3 patch_tesla_legacy_0x659_stalk_bits.py /data/openpilot/opendbc_repo/opendbc/safety/modes/tesla_legacy.h
"""

from __future__ import annotations
import argparse, re
from pathlib import Path


def ensure_tx_allowlist(src: str) -> str:
  # Find tesla_legacy_tx_msgs array and add 0x659 if missing.
  m = re.search(r"(static\s+const\s+CanMsg\s+tesla_legacy_tx_msgs\[\]\s*=\s*\{)([\s\S]*?)(\n\s*\};)", src)
  if not m:
    return src
  head, body, tail = m.group(1), m.group(2), m.group(3)
  if re.search(r"\b0x659\b", body):
    return src
  # Insert near other DAS msgs; prepend is fine.
  body2 = "\n  {0x659, 0, 8},  // OP internal fake DAS (blocked)\n" + body
  return src[:m.start()] + head + body2 + tail + src[m.end():]


def patch_tx_hook(src: str) -> str:
  # Replace any existing 0x659 handler, else insert near top of tx_hook.
  hook = re.search(r"(static\s+bool\s+tesla_legacy_tx_hook\s*\([^\)]*\)\s*\{\s*\n)", src)
  if not hook:
    raise SystemExit("couldn't find tesla_legacy_tx_hook")
  insert_at = hook.end(1)

  # If already has stalk_main parsing, skip.
  if "stalk_main" in src and "0x659U" in src:
    return src

  block = (
    "  // Unity parity: internal openpilot->panda carrier (0x659). Never forward to the car.\n"
    "  // Byte5 bits: bit7=autopilot_disabled, bit5=pedalEnabled, bit1=stalk_main(edge), bit0=stalk_cancel(edge)\n"
    "  if (msg->addr == 0x659U) {\n"
    "    const uint8_t b5 = (uint8_t)GET_BYTES(msg, 5U, 1U);\n"
    "    tesla_legacy_op_autopilot_disabled = (b5 & 0x80U) != 0U;\n"
    "    tesla_legacy_op_pedal_enabled = (b5 & 0x20U) != 0U;\n"
    "    const bool stalk_main = (b5 & 0x02U) != 0U;\n"
    "    const bool stalk_cancel = (b5 & 0x01U) != 0U;\n"
    "    if (stalk_main) {\n"
    "      pcm_cruise_check(true);\n"
    "    } else if (stalk_cancel) {\n"
    "      pcm_cruise_check(false);\n"
    "    }\n"
    "    return false;\n"
    "  }\n\n"
  )
  return src[:insert_at] + block + src[insert_at:]


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("path", type=Path)
  args = ap.parse_args()
  p: Path = args.path
  s = p.read_text(encoding="utf-8", errors="replace").replace("\r\n","\n").replace("\r","\n")

  # Remove illegal file-scope assignments (recurring)
  s = re.sub(r"^\s*(tesla_legacy_op_autopilot_disabled|tesla_legacy_op_pedal_enabled)\s*=\s*false\s*;\s*$", "", s, flags=re.M)

  out = ensure_tx_allowlist(s)
  out = patch_tx_hook(out)

  bak = p.with_suffix(p.suffix + ".bak_0x659_stalk_bits")
  if not bak.exists():
    bak.write_text(s, encoding="utf-8")
  p.write_text(out, encoding="utf-8")
  print(f"patched: {p} (backup: {bak})")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
