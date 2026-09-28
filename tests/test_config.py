"""Config dùng chung: các file sinh tự động phải khớp config (chống quên chạy gen_config.py)."""
from __future__ import annotations

import importlib.util

from hrc_common.config import ROOT_DIR, load_config

spec = importlib.util.spec_from_file_location("gen_config", ROOT_DIR / "scripts" / "gen_config.py")
gen_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen_config)


def _read(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_firmware_header_up_to_date():
    assert _read(gen_config.HEADER_PATH) == gen_config.render_header(), "Chạy: python scripts/gen_config.py"


def test_contracts_params_up_to_date():
    assert _read(gen_config.CONTRACTS_JSON_PATH) == gen_config.render_contracts_json(), \
        "Chạy: python scripts/gen_config.py"


def test_severities_within_contract_range():
    for k, v in load_config()["incident"].items():
        if k.startswith("severity_"):
            assert 1 <= v <= 3, k
