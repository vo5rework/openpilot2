#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
OUT="/data/openpilot/controls_mismatch_${TS}"
mkdir -p "$OUT"

# Try Android logcat first
if [ -x /system/bin/logcat ]; then
  /system/bin/logcat -d -v threadtime > "$OUT/logcat.txt" || true
else
  # fallback: whatever logcat is on PATH (may fail like you saw)
  logcat -d -v threadtime > "$OUT/logcat.txt" 2>"$OUT/logcat.err" || true
fi

# Grab common openpilot log dirs if they exist
for d in /data/log /data/openpilot /data/media/0 /data/media/0/realdata; do
  if [ -d "$d" ]; then
    find "$d" -maxdepth 3 -type f \( -name "system*.log*" -o -name "swaglog*" -o -name "*manager*.log*" \) \
      -print 2>/dev/null | head -n 200 > "$OUT/log_paths.txt" || true
  fi
done

# Grep controls mismatch across discovered paths (best-effort, bounded)
if [ -f "$OUT/log_paths.txt" ]; then
  while IFS= read -r f; do
    grep -ni "controls mismatch" "$f" 2>/dev/null || true
  done < "$OUT/log_paths.txt" > "$OUT/grep_controls_mismatch.txt" || true
fi

# tmux capture (best-effort)
if command -v tmux >/dev/null 2>&1; then
  tmux ls > "$OUT/tmux_ls.txt" 2>/dev/null || true
  tmux capture-pane -p -t comma:0.0 > "$OUT/tmux_0_0.txt" 2>/dev/null || true
  tmux capture-pane -p -t comma:0.1 > "$OUT/tmux_0_1.txt" 2>/dev/null || true
  tmux capture-pane -p -t comma:0.2 > "$OUT/tmux_0_2.txt" 2>/dev/null || true
  grep -ni "controls mismatch" "$OUT"/tmux_*.txt > "$OUT/grep_tmux_controls_mismatch.txt" 2>/dev/null || true
fi

tar -C "$(dirname "$OUT")" -czf "${OUT}.tgz" "$(basename "$OUT")"
echo "Wrote ${OUT}.tgz"
