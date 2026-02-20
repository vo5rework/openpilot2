#!/usr/bin/env bash
export NO_PREBUILT=1


DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

source "$DIR/launch_env.sh"
python3 -u system/tools/ensure_vision.py

function agnos_init {
  # TODO: move this to agnos
  sudo rm -f /data/etc/NetworkManager/system-connections/*.nmmeta

  # set success flag for current boot slot
  sudo abctl --set_success

  # TODO: do this without udev in AGNOS
  # udev does this, but sometimes we startup faster
  sudo chgrp gpu /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0
  sudo chmod 660 /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0

  # Check if AGNOS update is required
  if [ $(< /VERSION) != "$AGNOS_VERSION" ]; then
    AGNOS_PY="$DIR/system/hardware/tici/agnos.py"
    MANIFEST="$DIR/system/hardware/tici/agnos.json"
    if $AGNOS_PY --verify $MANIFEST; then
      sudo reboot
    fi
    $DIR/system/hardware/tici/updater $AGNOS_PY $MANIFEST
  fi
}

function launch {
  # Remove orphaned git lock if it exists on boot
  [ -f "$DIR/.git/index.lock" ] && rm -f $DIR/.git/index.lock

  # Check to see if there's a valid overlay-based update available. Conditions
  # are as follows:
  #
  # 1. The DIR init file has to exist, with a newer modtime than anything in
  #    the DIR Git repo. This checks for local development work or the user
  #    switching branches/forks, which should not be overwritten.
  # 2. The FINALIZED consistent file has to exist, indicating there's an update
  #    that completed successfully and synced to disk.

  if [ -f "${DIR}/.overlay_init" ]; then
    find ${DIR}/.git -newer ${DIR}/.overlay_init | grep -q '.' 2> /dev/null
    if [ $? -eq 0 ]; then
      echo "${DIR} has been modified, skipping overlay update installation"
    else
      if [ -f "${STAGING_ROOT}/finalized/.overlay_consistent" ]; then
        if [ ! -d /data/safe_staging/old_openpilot ]; then
          echo "Valid overlay update found, installing"
          LAUNCHER_LOCATION="${BASH_SOURCE[0]}"

          mv $DIR /data/safe_staging/old_openpilot
          mv "${STAGING_ROOT}/finalized" $DIR
          cd $DIR

          echo "Restarting launch script ${LAUNCHER_LOCATION}"
          unset AGNOS_VERSION
          exec "${LAUNCHER_LOCATION}"
        else
          echo "openpilot backup found, not updating"
          # TODO: restore backup? This means the updater didn't start after swapping
        fi
      fi
    fi
  fi

  # handle pythonpath
  ln -sfn $(pwd) /data/pythonpath
  export PYTHONPATH="$PWD"
  # --- SSH key provisioning (no UI/ADB) ---
  # If system/manager/github_user exists, fetch https://github.com/<user>.keys and enable SSH via Params.
  # A successful application renames github_user -> github_user.applied to avoid re-running.
  if [ -f "$DIR/system/manager/github_user" ] && [ ! -f "$DIR/system/manager/github_user.applied" ]; then
    GH_USER="$(head -n1 "$DIR/system/manager/github_user" | tr -d ' \t\r\n')"
    if [[ "$GH_USER" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$ ]]; then
      echo "Provisioning SSH keys for GitHub user: $GH_USER"
      if python3 "$DIR/tools/scripts/setup_ssh_keys.py" "$GH_USER"; then
        mv -f "$DIR/system/manager/github_user" "$DIR/system/manager/github_user.applied"
      else
        echo "SSH key provisioning failed (will retry next boot)"
      fi
    else
      echo "Invalid GitHub username in system/manager/github_user: '$GH_USER'"
    fi
  fi

  # Optional offline provisioning: place public keys in system/manager/authorized_keys (one key per line).
  if [ -f "$DIR/system/manager/authorized_keys" ] && [ ! -f "$DIR/system/manager/authorized_keys.applied" ]; then
    echo "Provisioning SSH keys from system/manager/authorized_keys"
    if python3 - <<'PY'
from openpilot.common.params import Params
import pathlib
p = pathlib.Path("system/manager/authorized_keys")
txt = p.read_text(encoding="utf-8", errors="ignore").strip()
if not txt:
  raise SystemExit(1)
Params().put_bool("SshEnabled", True)
Params().put("GithubSshKeys", txt)
Params().put("GithubUsername", "seed")
print("Set up ssh keys successfully (offline seed)")
PY
    then
      mv -f "$DIR/system/manager/authorized_keys" "$DIR/system/manager/authorized_keys.applied"
    else
      echo "Offline SSH key provisioning failed (will retry next boot)"
    fi
  fi


  # hardware specific init
  if [ -f /AGNOS ]; then
    agnos_init
  fi

  # write tmux scrollback to a file
  tmux capture-pane -pq -S-1000 > /tmp/launch_log

  # start manager
  cd system/manager
  if [ ! -f $DIR/prebuilt ]; then
    ./build.py
  fi
  ./manager.py

  # if broken, keep on screen error
  while true; do sleep 1; done
}

launch
