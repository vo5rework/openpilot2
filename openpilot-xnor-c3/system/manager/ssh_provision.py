# system/manager/ssh_provision.py
#!/usr/bin/env python3
"""
Install-time SSH provisioning without UI/ADB.

Seed sources (first match wins):
1) <repo_root>/system/manager/github_user                (GitHub username; fetches github.com/<user>.keys)
2) <repo_root>/system/manager/authorized_keys            (raw authorized_keys)
3) <repo_root>/system/manager/ssh_seed/github_username    (legacy)
4) <repo_root>/system/manager/ssh_seed/authorized_keys    (legacy)
5) /persist/ssh/github_username
6) /persist/ssh/authorized_keys
7) env OPENPILOT_SSH_GITHUB_USERNAME
8) env OPENPILOT_SSH_PUBLIC_KEYS

Writes Params:
- SshEnabled = true
- GithubSshKeys = <authorized_keys text>
- GithubUsername = <github user or "seed">

Renames the seed file to *.applied to avoid re-running.
Only public keys are accepted (ssh-*, ecdsa-*, sk-*).
"""

from __future__ import annotations

import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Optional

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

_ALLOWED_PREFIXES = (
  "ssh-ed25519",
  "ssh-rsa",
  "ecdsa-sha2-nistp256",
  "ecdsa-sha2-nistp384",
  "ecdsa-sha2-nistp521",
  "sk-ssh-ed25519@openssh.com",
  "sk-ecdsa-sha2-nistp256@openssh.com",
)

_GITHUB_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class Seed:
  kind: str  # "authorized_keys" | "github_username"
  payload: str
  source_path: Optional[str]


def _repo_root() -> str:
  # this file lives in system/manager
  return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_text_file(path: str, max_bytes: int = 64 * 1024) -> Optional[str]:
  try:
    st = os.stat(path)
    if st.st_size <= 0 or st.st_size > max_bytes:
      cloudlog.warning("ssh_provision: invalid seed size", path=path, size=st.st_size)
      return None
    with open(path, "rb") as f:
      raw = f.read(max_bytes + 1)
    if b"\x00" in raw:
      cloudlog.warning("ssh_provision: NUL byte in seed file", path=path)
      return None
    return raw.decode("utf-8", errors="ignore").strip()
  except FileNotFoundError:
    return None
  except Exception:
    cloudlog.exception("ssh_provision: failed reading seed file", path=path)
    return None


def _filter_public_keys(text: str) -> str:
  out: list[str] = []
  seen: set[str] = set()
  for ln in (text or "").splitlines():
    s = ln.strip()
    if not s or s.startswith("#"):
      continue
    if s.startswith(_ALLOWED_PREFIXES) and s not in seen:
      out.append(s)
      seen.add(s)
  return "\n".join(out).strip()


def _fetch_github_keys(username: str, timeout_s: float = 6.0) -> Optional[str]:
  url = f"https://github.com/{username}.keys"
  req = urllib.request.Request(url, headers={"User-Agent": "openpilot-ssh-provision"})
  try:
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
      body = resp.read(128 * 1024).decode("utf-8", errors="ignore").strip()
    keys = _filter_public_keys(body)
    return keys or None
  except Exception:
    cloudlog.exception("ssh_provision: github fetch failed", user=username)
    return None


def _mark_applied(path: str) -> None:
  try:
    applied = path + ".applied"
    if os.path.exists(path) and not os.path.exists(applied):
      os.rename(path, applied)
  except Exception:
    cloudlog.exception("ssh_provision: failed to mark applied", path=path)


def _find_seed() -> Optional[Seed]:
  rr = _repo_root()

  # NEW requested paths
  repo_user_new = os.path.join(rr, "system", "manager", "github_user")
  repo_auth_new = os.path.join(rr, "system", "manager", "authorized_keys")

  # Legacy paths (kept for compatibility)
  legacy_dir = os.path.join(rr, "system", "manager", "ssh_seed")
  repo_user_legacy = os.path.join(legacy_dir, "github_username")
  repo_auth_legacy = os.path.join(legacy_dir, "authorized_keys")

  persist_user = "/persist/ssh/github_username"
  persist_auth = "/persist/ssh/authorized_keys"

  # 1) repo github_user (NEW)
  txt = _read_text_file(repo_user_new)
  if txt:
    username = txt.splitlines()[0].strip()
    if _GITHUB_USER_RE.match(username):
      return Seed("github_username", username, repo_user_new)
    cloudlog.warning("ssh_provision: invalid github user", path=repo_user_new, user=username)

  # 2) repo authorized_keys (NEW)
  txt = _read_text_file(repo_auth_new)
  if txt:
    keys = _filter_public_keys(txt)
    if keys:
      return Seed("authorized_keys", keys, repo_auth_new)

  # 3) repo github_username (legacy)
  txt = _read_text_file(repo_user_legacy)
  if txt:
    username = txt.splitlines()[0].strip()
    if _GITHUB_USER_RE.match(username):
      return Seed("github_username", username, repo_user_legacy)

  # 4) repo authorized_keys (legacy)
  txt = _read_text_file(repo_auth_legacy)
  if txt:
    keys = _filter_public_keys(txt)
    if keys:
      return Seed("authorized_keys", keys, repo_auth_legacy)

  # 5) persist github_username
  txt = _read_text_file(persist_user)
  if txt:
    username = txt.splitlines()[0].strip()
    if _GITHUB_USER_RE.match(username):
      return Seed("github_username", username, persist_user)

  # 6) persist authorized_keys
  txt = _read_text_file(persist_auth)
  if txt:
    keys = _filter_public_keys(txt)
    if keys:
      return Seed("authorized_keys", keys, persist_auth)

  # 7/8) env fallbacks
  env_user = os.getenv("OPENPILOT_SSH_GITHUB_USERNAME", "").strip()
  if env_user and _GITHUB_USER_RE.match(env_user):
    return Seed("github_username", env_user, None)

  env_keys = os.getenv("OPENPILOT_SSH_PUBLIC_KEYS", "").strip()
  if env_keys:
    keys = _filter_public_keys(env_keys)
    if keys:
      return Seed("authorized_keys", keys, None)

  return None


def maybe_provision_ssh_keys() -> bool:
  """
  Returns True if provisioning ran and wrote Params.
  """
  params = Params()
  existing = (params.get("GithubSshKeys") or b"").decode("utf-8", errors="ignore").strip()
  if existing and params.get_bool("SshEnabled"):
    return False

  seed = _find_seed()
  if seed is None:
    return False

  username = "seed"
  keys: Optional[str] = None

  if seed.kind == "authorized_keys":
    keys = seed.payload
  else:
    username = seed.payload
    keys = _fetch_github_keys(username)

  if not keys:
    cloudlog.warning("ssh_provision: no keys resolved", kind=seed.kind, user=username)
    return False

  params.put_bool("SshEnabled", True)
  params.put("GithubSshKeys", keys)
  params.put("GithubUsername", username)
  cloudlog.info("ssh_provision: applied", kind=seed.kind, user=username, n=len(keys.splitlines()))

  if seed.source_path:
    _mark_applied(seed.source_path)

  return True
