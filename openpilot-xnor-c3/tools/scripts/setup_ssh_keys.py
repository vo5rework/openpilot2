#!/usr/bin/env python3
"""Provision SSH keys without UI.

Usage:
  tools/scripts/setup_ssh_keys.py <github username>

Effect:
- Fetches https://github.com/<username>.keys
- Writes Params: SshEnabled=true, GithubSshKeys=<keys>, GithubUsername=<username>

Exit codes:
- 0 on success
- 1 on usage / network / HTTP / empty keys
"""

from __future__ import annotations

import re
import sys

import requests

from openpilot.common.params import Params

_GH_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


def main() -> int:
  if len(sys.argv) != 2:
    print(f"Usage: {sys.argv[0]} <github username>")
    return 1

  username = sys.argv[1].strip()
  if not _GH_USER_RE.match(username):
    print("Invalid GitHub username")
    return 1

  try:
    resp = requests.get(f"https://github.com/{username}.keys", timeout=10)
  except Exception as e:
    print(f"Error getting public keys from github: {e}")
    return 1

  if resp.status_code != 200:
    print(f"Error getting public keys from github (HTTP {resp.status_code})")
    return 1

  keys_text = (resp.text or "").strip()
  if not keys_text:
    print("No keys returned from github")
    return 1

  params = Params()
  params.put_bool("SshEnabled", True)
  params.put("GithubSshKeys", keys_text)
  params.put("GithubUsername", username)
  print("Set up ssh keys successfully")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
