"""NGHIỆM THU PHASE 6 – mở khóa robot TỪ GIAO DIỆN dashboard (trình duyệt thật, Playwright).

Tự dựng môi trường riêng: deploy contract mới (Ignition) -> backend + API (cổng 8010) -> dashboard bản build
(vite preview, cổng 4174) -> simulator tạo sự cố THẬT (AI xác nhận -> reportIncident -> LOCKED) ->
trình duyệt kết nối ví SUPERVISOR -> bấm "Mở khóa" -> kiểm trạng thái on-chain = ACTIVE.

Ví: tests/wallet_shim.js (chuẩn EIP-1193 như MetaMask; Hardhat node tự ký cho tài khoản mặc định).
Cần: Hardhat node + Mosquitto đang chạy, `npm run build` trong dashboard/, `playwright install chromium`.
KHÔNG chạy song song với demo/e2e khác (dùng chung topic hrc/1/*). Chạy riêng: pytest -m ui
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request

import pytest

from conftest import TEST_KEY_HEX
from hrc_common.config import ROOT_DIR

SUPERVISOR = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"  # Hardhat account #2 = supervisor trong ignition module
DASH = ROOT_DIR / "dashboard"


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _has_playwright() -> bool:
    return importlib.util.find_spec("playwright") is not None


pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not _has_playwright(), reason="chưa cài playwright"),
    pytest.mark.skipif(not (DASH / "dist" / "index.html").exists(), reason="chưa build dashboard (npm run build)"),
    pytest.mark.skipif(not _port_open(8545), reason="không có Hardhat node"),
    pytest.mark.skipif(not _port_open(1883), reason="không có broker MQTT"),
    pytest.mark.skipif(shutil.which("node") is None, reason="không có Node.js"),
]


def _wait(cond, timeout: float, step: float = 0.3) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


@pytest.fixture
def stack(tmp_path, monkeypatch):
    import uvicorn

    from hrc_ai.inference import HrcDetector
    from hrc_backend.api import create_app
    from hrc_backend.chain import ChainClient
    from hrc_backend.hub import EventHub
    from hrc_backend.service import BackendService
    from hrc_backend.storage import LogStore

    monkeypatch.setenv("HRC_HMAC_KEY_HEX", TEST_KEY_HEX)
    spec = importlib.util.spec_from_file_location("e2e_local", ROOT_DIR / "scripts" / "e2e_local.py")
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    e2e.deploy_fresh()  # contract mới => trạng thái sạch

    key = bytes.fromhex(TEST_KEY_HEX)
    store, hub = LogStore(tmp_path / "ui.sqlite3"), EventHub()
    device = ChainClient.from_env("DEVICE")
    svc = BackendService(store, key, HrcDetector(), lambda: ChainClient.from_env("DEVICE"),
                         batch_interval_ms=3000, hub=hub)
    app = create_app(store, hub, key, lambda: ChainClient(), svc.status,
                     {"chain_id": device.chain_id, "address": device.address})
    api_port, ui_port = _free_port(), _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    svc.start()
    stop = threading.Event()
    threading.Thread(target=lambda: [svc.tick() for _ in iter(lambda: stop.wait(1.0), True)], daemon=True).start()
    # Chạy thẳng vite bằng node (1 tiến trình) để terminate() dừng được hẳn.
    # --host 127.0.0.1: trên Windows "localhost" của Node có thể chỉ là IPv6 ::1
    preview = subprocess.Popen(
        [shutil.which("node"), "node_modules/vite/bin/vite.js", "preview", "--host", "127.0.0.1", "--port", str(ui_port)],
        cwd=DASH, env={**os.environ, "HRC_API": f"http://127.0.0.1:{api_port}"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _wait(lambda: svc.ready.is_set() and _http_ok(f"http://127.0.0.1:{api_port}/api/health"), 20)
        assert _wait(lambda: _http_ok(f"http://127.0.0.1:{ui_port}/api/config"), 20), "vite preview không lên"
        yield {"svc": svc, "store": store, "device": device, "tmp": tmp_path, "url": f"http://127.0.0.1:{ui_port}"}
    finally:
        stop.set()
        preview.terminate()
        preview.wait(10)
        server.should_exit = True
        server_thread.join(10)
        svc.stop()
        store.close()


def test_unlock_robot_from_dashboard(stack):
    from playwright.sync_api import expect, sync_playwright

    from hrc_backend.chain import ChainClient
    from hrc_sim import cli as sim_cli

    device: object = stack["device"]
    ChainClient.from_env("OPERATOR").check_in(1)
    # Sự cố THẬT: người tiến vào vùng nguy hiểm -> AI xác nhận -> reportIncident -> LOCKED
    sim = threading.Thread(target=sim_cli.main, daemon=True,
                           args=(["--scenario", "approach", "--duration", "30", "--seed", "42",
                                  "--state-dir", str(stack["tmp"] / "nvs")],))
    sim.start()
    assert _wait(lambda: device.robot_info(1).state == 1, 25), "robot không bị LOCKED (AI/chuỗi)"

    shim = (ROOT_DIR / "tests" / "wallet_shim.js").read_text(encoding="utf-8")
    shim = shim.replace("__RPC__", "http://127.0.0.1:8545").replace("__ACCOUNT__", SUPERVISOR)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(shim)
        page.goto(stack["url"])

        expect(page.get_by_test_id("ws-status")).to_contain_text("real-time", timeout=10_000)
        expect(page.get_by_test_id("robot-1-zone")).to_be_visible(timeout=10_000)  # telemetry real-time đã tới
        page.get_by_test_id("connect-wallet").click()
        expect(page.get_by_test_id("role-SUPERVISOR")).to_be_visible(timeout=10_000)
        expect(page.get_by_test_id("robot-1-chain")).to_have_attribute("data-state", "1", timeout=15_000)
        expect(page.locator('[data-testid="incident-table"] tr[data-event="EmergencyStop"]')).to_have_count(1, timeout=10_000)

        # Supervisor chỉ mở khóa khi vùng làm việc đã an toàn (mở khi người còn trong vùng nguy hiểm
        # thì AI xác nhận lại ngay và hệ thống KHÓA LẠI – đúng thiết kế)
        expect(page.get_by_test_id("robot-1-zone")).to_have_attribute("data-tone", "good", timeout=20_000)
        page.get_by_test_id("unlock-1").click()  # <<< MỞ KHÓA TỪ GIAO DIỆN
        expect(page.get_by_test_id("unlock-1-tx")).to_contain_text("Đã mở khóa", timeout=20_000)
        assert device.robot_info(1).state == 0  # xác nhận độc lập trên chuỗi, ngay sau khi mở khóa
        expect(page.get_by_test_id("robot-1-chain")).to_have_attribute("data-state", "0", timeout=10_000)
        expect(page.locator('[data-testid="incident-table"] tr[data-event="Unlocked"]')).to_have_count(1, timeout=10_000)
        # Backend nghe event Unlocked -> gửi UNLOCK có ký tới thiết bị (hiện trong nhật ký real-time)
        expect(page.get_by_test_id("activity")).to_contain_text("Gửi lệnh UNLOCK", timeout=10_000)

        # Đối soát batch từ UI
        page.locator('[data-testid^="verify-"] button').first.click()
        expect(page.locator('[data-testid^="verify-"]').first).to_contain_text(re.compile("Khớp on-chain"), timeout=15_000)
        assert errors == [], f"lỗi JavaScript: {errors}"
        browser.close()

    # Bằng chứng on-chain: event Unlocked do ĐÚNG ví SUPERVISOR ký (kịch bản approach lặp lại nên sau đó
    # robot có thể bị khóa lại vì người quay vào vùng nguy hiểm – đó là hành vi đúng)
    unlocked = [e for e in device.get_events(0, device.block_number(), ("Unlocked",))]
    assert len(unlocked) == 1 and unlocked[0]["args"]["supervisor"].lower() == SUPERVISOR.lower()
    sim.join(40)


def test_unlock_rejected_for_non_supervisor(stack):
    """Ví KHÔNG có quyền SUPERVISOR: nút mở khóa bị vô hiệu + gợi ý rõ ràng (contract cũng sẽ từ chối)."""
    from playwright.sync_api import expect, sync_playwright

    stack["device"].report_incident(1, b"\x01" * 32, 1, "0x0000000000000000000000000000000000000000")
    outsider = "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc"  # Hardhat account #5 – không có vai trò
    shim = (ROOT_DIR / "tests" / "wallet_shim.js").read_text(encoding="utf-8")
    shim = shim.replace("__RPC__", "http://127.0.0.1:8545").replace("__ACCOUNT__", outsider)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.add_init_script(shim)
        page.goto(stack["url"])
        page.get_by_test_id("connect-wallet").click()
        expect(page.get_by_test_id("wallet")).to_contain_text("không có vai trò", timeout=10_000)
        expect(page.get_by_test_id("robot-1-chain")).to_have_attribute("data-state", "1", timeout=15_000)
        expect(page.get_by_test_id("unlock-1")).to_be_disabled()
        expect(page.get_by_test_id("robot-1-lockbox")).to_contain_text("không có quyền SUPERVISOR")
        browser.close()
