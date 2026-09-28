"""Fixture dùng chung cho pytest."""
from __future__ import annotations

import pytest

from backend_helpers import FakeClock  # noqa: F401  (re-export cho các file test)
from hrc_backend.storage import LogStore

# Khóa test cố định = byte 0x00..0x1f (KHÔNG dùng ngoài test)
TEST_KEY = bytes(range(32))
TEST_KEY_HEX = TEST_KEY.hex()


@pytest.fixture
def key() -> bytes:
    return TEST_KEY


@pytest.fixture
def store(tmp_path):
    s = LogStore(tmp_path / "test.sqlite3")
    yield s
    s.close()


@pytest.fixture
def clock():
    return FakeClock(1_790_000_000_000)


@pytest.fixture(scope="session")
def fw_host(tmp_path_factory):
    """Logic firmware C++ biên dịch cho máy tính (dùng chung giữa các file test)."""
    import fw_host as fh

    if fh.SKIP_REASON:
        pytest.skip(fh.SKIP_REASON)
    h = fh.build(tmp_path_factory.mktemp("fw"))
    yield h
    h.close()
