"""Repo hygiene: nothing about one person's network belongs in a public repo.

The rule this enforces: documentation, fixtures and examples describe connections
abstractly ("connect via `endpoint`"), never with a real address. A LAN address in
the tracked tree looks harmless and is not - it tells a reader which host to poke,
on which port, and that someone's inference box answers there. This repo is public,
so the check belongs in the suite rather than in anyone's memory.

Addresses that are fine to name:
  * RFC 5737 documentation space (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24),
    which routes nowhere - fixtures should use this.
  * 127.0.0.1 / ::1 / 0.0.0.0 / localhost, which are bind addresses, not hosts.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# RFC 1918 (private), RFC 4193 (ULA), and link-local. Documentation ranges are
# deliberately absent: they exist so that examples can name an address safely.
SUSPICIOUS = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3}"
    r"|f[cd][0-9a-f]{2}:[0-9a-f:]+)\b"
)
IGNORED_NAMES = {"test_repo_hygiene.py"}  # this file has to name the patterns to catch them


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
        ).stdout.decode("utf-8", "surrogateescape")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [REPO_ROOT / name for name in out.split("\0") if name]


def test_no_real_machine_addresses_in_the_tracked_tree():
    offenders = []
    for path in tracked_files():
        if path.name in IGNORED_NAMES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if SUSPICIOUS.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()[:90]}")
    assert not offenders, (
        "Tracked files must not name a real host on a real network. Use TEST-NET "
        "(192.0.2.0/24) in fixtures and a placeholder in docs:\n" + "\n".join(offenders)
    )


def test_documentation_space_is_allowed():
    assert not SUSPICIOUS.search("endpoint: http://198.51.100.7:8000/v1")
    assert not SUSPICIOUS.search("endpoint: http://127.0.0.1:8000/v1")
    assert not SUSPICIOUS.search("bind: 0.0.0.0")


def test_private_space_is_caught():
    assert SUSPICIOUS.search("endpoint: http://10.1.2.3:8000/v1")
    assert SUSPICIOUS.search("endpoint: http://192.168.1.5/v1")
    assert SUSPICIOUS.search("endpoint: http://172.16.0.9/v1")
    assert SUSPICIOUS.search("fd00::1")
