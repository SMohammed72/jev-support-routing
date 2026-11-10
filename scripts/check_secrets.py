#!/usr/bin/env python3
"""Fail if an API key or bearer token is present. Prints file/commit and rule name only, never the match.

  python3 scripts/check_secrets.py            # tracked + untracked-not-ignored files in the working tree
  python3 scripts/check_secrets.py --staged   # what is about to be committed (pre-commit hook)
  python3 scripts/check_secrets.py --history  # every commit on every ref

If OPENROUTER_API_KEY is set in the environment, its exact value is also searched for (read from the
environment inside this process, never passed on a command line or printed)."""
from __future__ import annotations

import os
import re
import subprocess
import sys

RULES = {
    "openrouter-key": re.compile(r"sk-or-(?:v1-)?[A-Za-z0-9]{32,}"),
    "generic-sk-key": re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}"),
    "bearer-token": re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{24,}"),
    "env-assignment": re.compile(r"OPENROUTER_API_KEY\s*=\s*['\"]?(?!your-key-here|\$|<)[A-Za-z0-9_-]{16,}"),
}


def git(*a: str) -> str:
    return subprocess.run(["git", *a], check=True, capture_output=True, text=True, errors="replace").stdout


def scan(label: str, text: str, live_key: str) -> list[str]:
    hits = [f"{label}: {name}" for name, rx in RULES.items() if rx.search(text)]
    if live_key and live_key in text:
        hits.append(f"{label}: exact OPENROUTER_API_KEY value")
    return hits


def main() -> int:
    live_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    live_key = live_key if len(live_key) >= 16 else ""
    hits: list[str] = []
    if "--history" in sys.argv:
        for sha in git("rev-list", "--all").split():
            hits += scan(f"commit {sha[:10]}", git("show", "--format=%B", "--no-color", "-p", sha), live_key)
    elif "--staged" in sys.argv:
        hits += scan("staged diff", git("diff", "--cached", "--no-color"), live_key)
    else:
        for f in git("ls-files", "--cached", "--others", "--exclude-standard").splitlines():
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    hits += scan(f, fh.read(), live_key)
            except (FileNotFoundError, IsADirectoryError):
                pass
    if hits:
        print("SECRET SCAN FAILED (matches not shown):", *sorted(set(hits)), sep="\n  ")
        return 1
    print("secret scan clean" + (" (including exact key value)" if live_key else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
