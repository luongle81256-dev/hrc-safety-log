"""Đọc biến môi trường từ .env ở gốc repo (dùng chung cho simulator, backend, script).

Hồ sơ mạng: đặt HRC_NETWORK=sepolia => nạp `.env.sepolia` TRƯỚC rồi mới tới `.env`
(thứ tự ưu tiên: biến môi trường thật > .env.<mạng> > .env). Nhờ vậy cấu hình local trong `.env`
không bị đụng tới khi chạy trên testnet.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .config import ROOT_DIR


def network() -> str:
    return os.getenv("HRC_NETWORK", "local").strip().lower() or "local"


def load_env() -> None:
    """Nạp .env (và .env.<HRC_NETWORK> nếu có). Biến đã có trong môi trường không bị ghi đè."""
    net = network()
    if net != "local":
        profile = ROOT_DIR / f".env.{net}"
        if not profile.exists():
            raise FileNotFoundError(f"HRC_NETWORK={net} nhưng thiếu {profile.name} – copy từ {profile.name}.example")
        load_dotenv(profile, override=False)
    load_dotenv(ROOT_DIR / ".env", override=False)


@dataclass(frozen=True)
class MqttSettings:
    host: str
    port: int
    username: str | None
    password: str | None


def mqtt_settings() -> MqttSettings:
    load_env()
    return MqttSettings(
        host=os.getenv("MQTT_HOST", "localhost"),
        port=int(os.getenv("MQTT_PORT", "1883")),
        username=os.getenv("MQTT_USERNAME") or None,
        password=os.getenv("MQTT_PASSWORD") or None,
    )


def repo_path(value: str) -> Path:
    """Đường dẫn tương đối trong .env được hiểu là tương đối với gốc repo."""
    p = Path(value)
    return p if p.is_absolute() else ROOT_DIR / p
