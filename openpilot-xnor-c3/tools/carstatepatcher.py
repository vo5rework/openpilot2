#!/usr/bin/env python3
"""
/data/openpilot/selfdrive/debug/patch_tesla_carstate_cruiseset.py

Fix Tesla cruise-set half-scale (23mph displayed as ~12mph) without touching the DBC:
- Use a robust picker for cruise setpoint (DI_cruiseSet vs DI_digitalSpeed vs DI_cruiseSet*2).
- Fix UI_driverAssistMapData key typo: UI_mapSpeedLimitType.

This script is designed to be "no roulette":
- creates a timestamped backup
- validates required patterns exist before modifying
- runs py_compile after writing
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path


CRUISE_BLOCK_MARKER = "# Cruise set speed (DI_cruiseSet); required for speed-limit stalk sync"


PICKER_METHOD = r"""
  def _pick_stock_cruise_set_u(self, di_state: dict, v_ego_ms: float, speed_units: str) -> tuple[float, str]:
    """Pick Tesla cruise setpoint in MPH/KPH without changing the DBC.

    On some setups, DI_cruiseSet decodes like ~0.5*vEgo which makes the UI "max speed" look ~half.
    We select among:
      - DI_cruiseSet
      - DI_digitalSpeed (some cars mirror setpoint here)
      - 2*DI_cruiseSet (only when it plausibly matches speed or the other field)
    """
    try:
      a = float(di_state.get("DI_cruiseSet", 0.0) or 0.0)
    except Exception:
      a = 0.0
    try:
      b = float(di_state.get("DI_digitalSpeed", 0.0) or 0.0)
    except Exception:
      b = 0.0

    uom = "KPH" if speed_units == "KPH" else "MPH"
    ms_to_u = CV.MS_TO_KPH if uom == "KPH" else CV.MS_TO_MPH
    v_u = float(v_ego_ms) * ms_to_u
    last_u = float(getattr(self, "stock_cruise_set_speed_ms", 0.0) or 0.0) * ms_to_u

    candidates: list[tuple[float, str]] = []
    if a > 0.0:
      candidates.append((a, "DI_cruiseSet"))
    if b > 0.0:
      candidates.append((b, "DI_digitalSpeed"))

    # Add corrected half-scale candidate only when plausible.
    if a > 0.0:
      thr = max(1.5, 0.06 * max(v_u, 1.0))
      # If a is suspiciously low and doubling lands near v or b, consider it.
      if (a < 0.75 * max(v_u, 1.0)) and (abs((2.0 * a) - v_u) <= thr or (b > 0.0 and abs((2.0 * a) - b) <= thr)):
        candidates.append((2.0 * a, "DI_cruiseSet_x2"))

    if not candidates:
      return 0.0, "none"
    if len(candidates) == 1:
      return float(candidates[0][0]), str(candidates[0][1])

    def _integerish_penalty(x: float) -> float:
      # Cruise setpoints are usually integer-ish.
      frac = abs(x - round(x))
      return 1.5 if frac > 0.05 else 0.0

    def _half_speed_penalty(x: float) -> float:
      # Strong penalty if a candidate matches ~0.5*v (typical half-scale bug).
      thr = max(1.5, 0.06 * max(v_u, 1.0))
      return 4.0 if abs(x - (0.5 * v_u)) <= thr else 0.0

    def _stability_penalty(x: float) -> float:
      # Prefer candidates that don't bounce with instantaneous speed.
      if last_u <= 1.0:
        return 0.0
      return min(abs(x - last_u) / 5.0, 2.0)

    def score(x: float) -> float:
      if x <= 0.0:
        return 1e9
      if math.isnan(x) or math.isinf(x):
        return 1e9
      return _integerish_penalty(x) + _half_speed_penalty(x) + _stability_penalty(x)

    best_val, best_src, best_s = 0.0, "none", 1e9
    for val, src in candidates:
      s = score(float(val))
      if s < best_s:
        best_val, best_src, best_s = float(val), str(src), float(s)

    return best_val, best_src
""".strip(
    "\n"
)


def _ensure_import_math(src: str) -> str:
    if re.search(r"^\s*import\s+math\s*$", src, flags=re.MULTILINE):
        return src

    # Insert after the last "import ..." in the header block.
    m = list(re.finditer(r"^(?:from\s+\S+\s+import\s+.+|import\s+.+)\s*$", src, flags=re.MULTILINE))
    if not m:
        raise RuntimeError("Could not find any import lines to anchor `import math`.")
    last = m[-1]
    insert_at = last.end()
    return src[:insert_at] + "\nimport math\n" + src[insert_at:]


def _replace_picker_method(src: str) -> str:
    # Replace the *class method* version (2-space indent) if present.
    start = src.find("\n  def _pick_stock_cruise_set_u(")
    if start < 0:
        raise RuntimeError("Could not find `  def _pick_stock_cruise_set_u(` in carstate.py")

    # Find next method at same indentation.
    after_start = src.find("\n  def ", start + 5)
    if after_start < 0:
        raise RuntimeError("Could not find end of picker method (next `\\n  def `).")

    # Keep surrounding spacing consistent: ensure there is a blank line before/after.
    new_block = "\n\n" + PICKER_METHOD + "\n\n"
    return src[:start] + new_block + src[after_start:]


def _replace_cruise_set_block(lines: list[str]) -> list[str]:
    idx = None
    for i, ln in enumerate(lines):
        if CRUISE_BLOCK_MARKER in ln:
            idx = i
            break
    if idx is None:
        raise RuntimeError(f"Could not find cruise-set marker line: {CRUISE_BLOCK_MARKER!r}")

    # Replace until next blank line that follows at least one line of the block,
    # or until we hit a new section comment (line starting with '    #').
    start = idx
    end = start + 1
    while end < len(lines):
        ln = lines[end]
        if end > start + 2 and (ln.strip() == ""):
            end += 1
            break
        if end > start + 2 and re.match(r"^\s{4}#\s+", ln):
            break
        end += 1

    indent = "    "
    new = [
        lines[start],  # keep the marker comment line as-is
        f"{indent}di_state = cp_party.vl[\"DI_state\"]\n",
        f"{indent}speed_units = \"KPH\" if int(di_state.get(\"DI_speedUnits\", 0) or 0) == 1 else \"MPH\"\n",
        f"{indent}self.speed_units = speed_units\n",
        "\n",
        f"{indent}cruise_enabled = int(di_state.get(\"DI_cruiseState\", 0) or 0) != 0\n",
        f"{indent}self.stock_cruise_enabled = bool(cruise_enabled)\n",
        "\n",
        f"{indent}cruise_set_u, self._cruise_set_src = self._pick_stock_cruise_set_u(di_state, float(ret.vEgo), speed_units)\n",
        f"{indent}u_to_ms = CV.KPH_TO_MS if speed_units == \"KPH\" else CV.MPH_TO_MS\n",
        f"{indent}self.stock_cruise_set_speed_ms = float(cruise_set_u) * u_to_ms\n",
        "\n",
        f"{indent}if cruise_set_u > 0.0:\n",
        f"{indent}  ret.cruiseState.speed = float(self.stock_cruise_set_speed_ms)\n",
        "\n",
        f"{indent}# Optional debug once per ~2s when speed-limit sync is enabled\n",
        f"{indent}if getattr(self._tinkla, \"adjust_acc_with_speed_limit\", False) and (self._param_frame % 100 == 0):\n",
        f"{indent}  try:\n",
        f"{indent}    conv = 2.2369362920544 if speed_units == \"MPH\" else 3.6\n",
        f"{indent}    cloudlog.info(\n",
        f"{indent}      f\"[XNOR_CS] uom={{speed_units}} src={{self._cruise_set_src}} \"\n",
        f"{indent}      f\"v={{float(ret.vEgo)*conv:.1f}} rawCruiseSet={{float(di_state.get('DI_cruiseSet', 0.0) or 0.0):.1f}} \"\n",
        f"{indent}      f\"rawDigital={{float(di_state.get('DI_digitalSpeed', 0.0) or 0.0):.1f}} \"\n",
        f"{indent}      f\"picked={{float(self.stock_cruise_set_speed_ms)*conv:.1f}}\"\n",
        f"{indent}    )\n",
        f"{indent}  except Exception:\n",
        f"{indent}    pass\n",
        "\n",
    ]

    return lines[:start] + new + lines[end:]


def _fix_map_key_typo(src: str) -> str:
    # Only replace the specific get(...) occurrence.
    return src.replace('map_data.get("UI_mapSpeedLimit", 0)', 'map_data.get("UI_mapSpeedLimitType", 0)')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--file",
        default="/data/openpilot/opendbc/car/tesla/carstate.py",
        help="Path to tesla carstate.py on device",
    )
    args = ap.parse_args()

    p = Path(args.file)
    if not p.exists():
        print(f"ERROR: file not found: {p}", file=sys.stderr)
        return 2

    original = p.read_text(encoding="utf-8", errors="replace")

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, backup)
    print(f"Backup written: {backup}")

    patched = original
    patched = _ensure_import_math(patched)
    patched = _replace_picker_method(patched)
    patched = _fix_map_key_typo(patched)

    lines = patched.splitlines(keepends=True)
    lines = _replace_cruise_set_block(lines)
    patched = "".join(lines)

    if patched == original:
        print("No changes made (unexpected).", file=sys.stderr)
        return 3

    p.write_text(patched, encoding="utf-8")
    print(f"Patched written: {p}")

    # Syntax check (prevents abstract/indentation regressions).
    res = subprocess.run([sys.executable, "-m", "py_compile", str(p)], capture_output=True, text=True)
    if res.returncode != 0:
        print("py_compile FAILED. Restoring backup.", file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        shutil.copy2(backup, p)
        print(f"Restored: {p}")
        return 4

    print("py_compile OK.")
    print("Done. Reboot device or restart manager to load changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
