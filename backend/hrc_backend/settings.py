"""Cấu hình backend đọc từ .env (bí mật) + config dùng chung (tham số)."""
from __future__ import annotations

import os
from pathlib import Path

from hrc_common.config import load_config
from hrc_common.env import load_env, repo_path
from hrc_common.protocol import load_key

from .storage import LogStore


def sqlite_path() -> Path:
    load_env()
    return repo_path(os.getenv("SQLITE_PATH", "backend/data/hrc_log.sqlite3"))


def open_store(path: Path | None = None) -> LogStore:
    cfg = load_config()
    return LogStore(path or sqlite_path(), cfg["security"]["rejected_payload_max_bytes"])


def hmac_key() -> bytes:
    load_env()
    return load_key()
