"""Configuration out of the environment, with a .env file for local use.

Credentials never live in source. `.env` holds the real values and is ignored by
git; `.env.example` is the committed template that documents every key.

Deliberately stdlib-only. One more pip dependency to read a handful of
`KEY=value` lines is not worth the install, and this file has to import cleanly
before Django is configured.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """Populate os.environ from a .env file, if one is present.

    A real environment variable always wins over the file, so a container, CI
    job or `set CCT_DB_SERVER=...` can override without anyone editing .env.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def env_list(name: str, default: str = "") -> list[str]:
    return [p.strip() for p in env(name, default).split(",") if p.strip()]
