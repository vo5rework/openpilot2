#!/usr/bin/env python3
"""
Ensure the visionipc native extension exists before OpenPilot services start.

Fixes intermittent boot crashes like:
  ModuleNotFoundError: No module named 'msgq.visionipc.visionipc_pyx'

Behavior:
  - If msgq/visionipc/visionipc_pyx*.so exists: exit 0
  - Else: run a targeted scons build for msgq/visionipc/visionipc_pyx.so
  - If still missing: run full scons build
  - If still missing: exit 1 (fail fast; prevents later confusing crashes)

Env:
  - OP_SKIP_ENSURE_VISIONIPC=1   -> skip this check/build
  - SCONS_JOBS=8                 -> override job count
  - SCONS_CACHE=/data/scons_cache -> enables cache for faster rebuilds
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path


REQUIRED_GLOBS: tuple[str, ...] = (
    "msgq/visionipc/visionipc_pyx*.so",
)

TARGET_SO: str = "msgq/visionipc/visionipc_pyx.so"


def repo_root() -> Path:
    # /data/openpilot/system/tools/ensure_visionipc.py -> repo root is 2 parents up
    return Path(__file__).resolve().parents[2]


def has_required_artifacts(root: Path) -> bool:
    return any(glob.glob(str(root / pattern)) for pattern in REQUIRED_GLOBS)


def run_cmd(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(cmd, cwd=str(cwd), env=env, check=True)


def ensure_scons_available(env: dict[str, str]) -> None:
    if shutil.which("scons", path=env.get("PATH")) is None:
        raise RuntimeError("scons not found in PATH")


def main() -> int:
    if os.environ.get("OP_SKIP_ENSURE_VISIONIPC") == "1":
        return 0

    root = repo_root()
    env = os.environ.copy()

    # Cache makes rebuilds much faster after updater cleans artifacts.
    env.setdefault("SCONS_CACHE", "/data/scons_cache")
    try:
        Path(env["SCONS_CACHE"]).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    if has_required_artifacts(root):
        return 0

    jobs = env.get("SCONS_JOBS") or str(os.cpu_count() or 4)

    try:
        ensure_scons_available(env)
    except Exception as e:
        print(f"[ensure_visionipc] ERROR: {e}", file=sys.stderr)
        return 1

    print("[ensure_visionipc] visionipc .so missing; rebuilding...", file=sys.stderr)

    # 1) Targeted build (fast path)
    try:
        run_cmd(["scons", f"-j{jobs}", TARGET_SO], cwd=root, env=env)
    except subprocess.CalledProcessError:
        # Some trees may not expose the .so as a direct target; fall back.
        pass

    if has_required_artifacts(root):
        print("[ensure_visionipc] OK (targeted build)", file=sys.stderr)
        return 0

    # 2) Full build fallback
    try:
        run_cmd(["scons", f"-j{jobs}"], cwd=root, env=env)
    except subprocess.CalledProcessError as e:
        print(f"[ensure_visionipc] ERROR: full scons build failed: {e}", file=sys.stderr)

    if has_required_artifacts(root):
        print("[ensure_visionipc] OK (full build)", file=sys.stderr)
        return 0

    print("[ensure_visionipc] ERROR: still missing after rebuild", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
