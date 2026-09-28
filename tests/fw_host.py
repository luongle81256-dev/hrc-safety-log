"""Biên dịch logic firmware C++ (src/ của ESP32) thành chương trình chạy trên máy tính – dùng chung cho các test.

Chương trình nhận lệnh qua stdin, mỗi dòng 1 lệnh, in 1 dòng kết quả (xem iot_code/firmware/host_test/host_main.cpp).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from hrc_common.config import ROOT_DIR

FW = ROOT_DIR / "iot_code" / "firmware"
AJSON = FW / ".pio" / "libdeps" / "esp32dev" / "ArduinoJson" / "src"
SOURCES = [FW / "src" / f for f in ("hrc_logic.cpp", "hrc_crypto_common.cpp", "hrc_cmd.cpp", "hrc_features.cpp",
                                    "hrc_int8_nn.cpp")]
SOURCES += [FW / "host_test" / f for f in ("sha256_ref.cpp", "host_main.cpp")]

SKIP_REASON = ("không có g++" if shutil.which("g++") is None
               else "chưa có ArduinoJson – chạy `pio run` trong iot_code/firmware" if not AJSON.exists() else None)


class Host:
    """Tiến trình host_test: gửi 1 dòng lệnh, nhận 1 dòng kết quả."""

    def __init__(self, exe: Path):
        self.p = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def __call__(self, line: str) -> str:
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        return self.p.stdout.readline().strip()

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(5)


def build(out_dir: Path) -> Host:
    exe = out_dir / "host_test.exe"
    cmd = ["g++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-DARDUINOJSON_USE_LONG_LONG=1",
           f"-I{FW / 'include'}", f"-I{FW / 'src'}", f"-I{AJSON}", *map(str, SOURCES), "-o", str(exe)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"biên dịch logic firmware lỗi:\n{r.stderr}")
    return Host(exe)
