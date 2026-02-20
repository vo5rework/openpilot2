#!/usr/bin/env python3
"""
panda_board_diff.py

Simple, dependency-free comparer for panda/board trees (Unity vs XNOR, etc.).

Examples:

  # Compare two working trees
  python3 panda_board_diff.py /data/panda_unity/board /data/panda_xnor/board

  # Compare just drivers + boards
  python3 panda_board_diff.py /data/panda_unity/board /data/panda_xnor/board --only drivers boards

  # Show only files that mention CAN2/transceiver-related terms
  python3 panda_board_diff.py /data/panda_unity/board /data/panda_xnor/board --grep CAN2 --grep transceiver --grep STBY

This does NOT require git. It just compares file contents.
"""

from __future__ import annotations
import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

TEXT_EXTS = {".c", ".h", ".hpp", ".s", ".S", ".mk", ".ld", ".py", ".txt", ".md", ".inc", ".cfg"}

def sha256_file(p: Path) -> str:
  h = hashlib.sha256()
  with p.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
      h.update(chunk)
  return h.hexdigest()

def iter_files(root: Path) -> Iterable[Path]:
  for dirpath, _, filenames in os.walk(root):
    for fn in filenames:
      p = Path(dirpath) / fn
      # skip build artifacts
      if any(part in {".git", "obj", "build", "out"} for part in p.parts):
        continue
      yield p

def rel(p: Path, root: Path) -> str:
  return str(p.relative_to(root))

def read_text_best_effort(p: Path) -> str:
  try:
    return p.read_text(errors="replace")
  except Exception:
    return ""

def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("a", type=Path, help="First board directory (e.g. unity/panda/board)")
  ap.add_argument("b", type=Path, help="Second board directory (e.g. xnor/panda/board)")
  ap.add_argument("--only", nargs="*", default=None, help="Only compare these subdirs (e.g. drivers boards)")
  ap.add_argument("--grep", action="append", default=[], help="Only report diffs if file contains this string (repeatable)")
  ap.add_argument("--show", action="store_true", help="Print a short content snippet around first match for --grep terms")
  args = ap.parse_args()

  a_root = args.a.resolve()
  b_root = args.b.resolve()

  if not a_root.is_dir() or not b_root.is_dir():
    print("Both inputs must be directories.", file=sys.stderr)
    return 2

  def allowed(relpath: str) -> bool:
    if args.only is None:
      return True
    parts = Path(relpath).parts
    return len(parts) > 0 and parts[0] in set(args.only)

  a_files = {rel(p, a_root): p for p in iter_files(a_root) if allowed(rel(p, a_root))}
  b_files = {rel(p, b_root): p for p in iter_files(b_root) if allowed(rel(p, b_root))}

  all_keys = sorted(set(a_files.keys()) | set(b_files.keys()))
  changed: List[str] = []
  added: List[str] = []
  removed: List[str] = []

  for k in all_keys:
    pa = a_files.get(k)
    pb = b_files.get(k)
    if pa is None:
      added.append(k)
      continue
    if pb is None:
      removed.append(k)
      continue

    # quick hash compare
    try:
      ha = sha256_file(pa)
      hb = sha256_file(pb)
    except Exception:
      ha = hb = ""

    if ha == hb:
      continue

    # optional grep filtering
    if args.grep:
      txt_a = read_text_best_effort(pa) if pa.suffix in TEXT_EXTS else ""
      txt_b = read_text_best_effort(pb) if pb.suffix in TEXT_EXTS else ""
      merged = txt_a + "\n" + txt_b
      if not any(g in merged for g in args.grep):
        continue
    changed.append(k)

  print(f"Compared:\n  A={a_root}\n  B={b_root}\n")
  if args.only is not None:
    print(f"Subdirs: {args.only}\n")
  if args.grep:
    print(f"Grep filter: {args.grep}\n")

  if added:
    print("Only in B (added):")
    for k in added:
      print("  +", k)
    print()
  if removed:
    print("Only in A (removed):")
    for k in removed:
      print("  -", k)
    print()

  print(f"Changed files: {len(changed)}")
  for k in changed:
    print("  *", k)

  if args.show and args.grep and changed:
    print("\n--- Snippets (first match per file) ---")
    for k in changed[:50]:
      pa = a_files.get(k)
      pb = b_files.get(k)
      for label, p in (("A", pa), ("B", pb)):
        if p is None or p.suffix not in TEXT_EXTS:
          continue
        txt = read_text_best_effort(p)
        for g in args.grep:
          idx = txt.find(g)
          if idx != -1:
            start = max(0, idx - 120)
            end = min(len(txt), idx + 240)
            snippet = txt[start:end].replace("\n", "\\n")
            print(f"{k} [{label}] ...{snippet}...")
            break
        else:
          continue
        break
    print("--- end ---")

  return 0

if __name__ == "__main__":
  raise SystemExit(main())
