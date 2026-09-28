"""Kết nối contract HRCSafetyLog bằng web3.py v7 (dùng cho backend, script demo, test).

- Ký giao dịch cục bộ bằng private key (chạy được cả Hardhat node lẫn Sepolia).
- Mô phỏng (.call) TRƯỚC khi gửi để bắt revert và giải mã custom error thành tên dễ đọc.
- ABI lấy từ artifact Hardhat; địa chỉ lấy từ CONTRACT_ADDRESS hoặc file deploy của Ignition.
- web3 không đảm bảo an toàn đa luồng => mỗi luồng tạo ChainClient riêng.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from eth_abi import decode as abi_decode
from eth_account import Account
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractCustomError, ContractLogicError, ContractPanicError
from web3.logs import DISCARD

from hrc_common.config import ROOT_DIR, load_config
from hrc_common.env import load_env

log = logging.getLogger("hrc-chain")

CONTRACTS_DIR = ROOT_DIR / "contracts"
ARTIFACT_PATH = CONTRACTS_DIR / "artifacts" / "contracts" / "HRCSafetyLog.sol" / "HRCSafetyLog.json"
IGNITION_KEY = "HRCSafetyLogModule#HRCSafetyLog"
STATE_ACTIVE, STATE_LOCKED = 0, 1
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
EVENT_NAMES = ("LogAnchored", "EmergencyStop", "Unlocked", "ViolationRecorded", "OperatorBlocked",
               "OperatorCheckedIn", "OperatorCheckedOut", "AuditRecorded")


class ChainError(RuntimeError):
    """Lỗi khi làm việc với chuỗi. `error_name` là tên custom error của contract nếu giải mã được."""

    def __init__(self, message: str, error_name: str | None = None):
        super().__init__(message)
        self.error_name = error_name


@dataclass(frozen=True, slots=True)
class RobotInfo:
    registered: bool
    device: str
    state: int
    last_batch_id: int
    last_to_seq: int
    current_operator: str


@dataclass(frozen=True, slots=True)
class TxResult:
    tx_hash: str
    block_number: int
    gas_used: int
    receipt: object


def load_abi() -> list:
    if not ARTIFACT_PATH.exists():
        raise ChainError(f"Chưa có artifact {ARTIFACT_PATH}. Chạy: cd contracts; npx hardhat build")
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))["abi"]


def resolve_address(chain_id: int) -> str:
    load_env()
    if os.getenv("CONTRACT_ADDRESS"):
        return Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"])
    path = CONTRACTS_DIR / "ignition" / "deployments" / f"chain-{chain_id}" / "deployed_addresses.json"
    if not path.exists():
        raise ChainError(f"Chưa deploy lên chain {chain_id} (thiếu {path.relative_to(ROOT_DIR)}). "
                         "Chạy: cd contracts; npx hardhat ignition deploy ignition/modules/HRCSafetyLog.ts --network localhost")
    return Web3.to_checksum_address(json.loads(path.read_text(encoding="utf-8"))[IGNITION_KEY])


def rpc_url() -> str:
    load_env()
    return os.getenv("CHAIN_RPC_URL") or os.getenv("LOCAL_RPC_URL") or "http://127.0.0.1:8545"


class ChainClient:
    def __init__(self, url: str | None = None, private_key: str | None = None, address: str | None = None):
        self.url = url or rpc_url()
        self.w3 = Web3(Web3.HTTPProvider(self.url, request_kwargs={"timeout": 30}))
        if not self.w3.is_connected():
            raise ChainError(f"Không kết nối được RPC {self.url}. Local: cd contracts; npx hardhat node")
        self.chain_id = self.w3.eth.chain_id
        self.abi = load_abi()
        self.address = Web3.to_checksum_address(address) if address else resolve_address(self.chain_id)
        if self.w3.eth.get_code(self.address) in (b"", b"\x00"):
            raise ChainError(f"Không có contract ở {self.address} trên chain {self.chain_id} (node đã khởi động lại?)"
                             " – hãy deploy lại bằng Ignition (--reset)")
        self.contract = self.w3.eth.contract(address=self.address, abi=self.abi)
        self.account = Account.from_key(private_key) if private_key else None
        self.tx_timeout_s = load_config()["chain"]["tx_timeout_s"]
        # selector 4 byte -> (tên lỗi, tên tham số, kiểu tham số) để giải mã revert
        self._errors: dict[bytes, tuple[str, list[str], list[str]]] = {}
        for item in self.abi:
            if item.get("type") == "error":
                types = [i["type"] for i in item["inputs"]]
                sig = f"{item['name']}({','.join(types)})"
                self._errors[function_signature_to_4byte_selector(sig)] = (
                    item["name"], [i["name"] for i in item["inputs"]], types)

    @classmethod
    def from_env(cls, role: str = "DEVICE") -> ChainClient:
        """Tạo client ký bằng {role}_PRIVATE_KEY trong .env (DEVICE | SUPERVISOR | OPERATOR | AUDITOR | ADMIN)."""
        load_env()
        key = os.getenv(f"{role}_PRIVATE_KEY")
        if not key:
            raise ChainError(f"Thiếu {role}_PRIVATE_KEY trong .env")
        return cls(private_key=key)

    @property
    def sender(self) -> str:
        if self.account is None:
            raise ChainError("Client chỉ đọc (không có private key)")
        return self.account.address

    # ---------------- giải mã lỗi ----------------
    def decode_error(self, data: str | bytes | None) -> tuple[str | None, str]:
        if isinstance(data, str):
            data = bytes.fromhex(data.removeprefix("0x")) if data.startswith("0x") else None
        if not data or len(data) < 4 or data[:4] not in self._errors:
            return None, "không giải mã được lỗi"
        name, arg_names, types = self._errors[data[:4]]
        values = abi_decode(types, data[4:])
        shown = ", ".join(f"{n}={'0x' + v.hex() if isinstance(v, bytes) else v}" for n, v in zip(arg_names, values))
        return name, f"{name}({shown})"

    def _raise_from(self, fn_name: str, e: Exception) -> None:
        data = getattr(e, "data", None)
        if data is None and e.args:
            data = e.args[0]
        name, text = self.decode_error(data if isinstance(data, (str, bytes)) else None)
        raise ChainError(f"{fn_name} bị từ chối: {text if name else e}", name) from e

    # ---------------- ghi ----------------
    def send(self, fn_name: str, *args) -> TxResult:
        """Mô phỏng -> ký -> gửi -> chờ receipt. Revert được đổi thành ChainError có tên lỗi."""
        fn = getattr(self.contract.functions, fn_name)(*args)
        try:
            fn.call({"from": self.sender})
        except (ContractCustomError, ContractLogicError, ContractPanicError) as e:
            self._raise_from(fn_name, e)
        tx = fn.build_transaction({
            "from": self.sender,
            "nonce": self.w3.eth.get_transaction_count(self.sender, "pending"),
            "chainId": self.chain_id,
        })
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=self.tx_timeout_s)
        if receipt["status"] != 1:
            raise ChainError(f"{fn_name}: giao dịch {Web3.to_hex(tx_hash)} thất bại (status 0)")
        return TxResult(Web3.to_hex(tx_hash), receipt["blockNumber"], receipt["gasUsed"], receipt)

    def anchor_log(self, robot_id: int, batch_id: int, root: bytes, from_seq: int, to_seq: int) -> TxResult:
        return self.send("anchorLog", robot_id, batch_id, root, from_seq, to_seq)

    def report_incident(self, robot_id: int, evidence: bytes, severity: int, operator: str) -> tuple[TxResult, int]:
        """Trả về (kết quả tx, incidentId trên chuỗi) – incidentId đọc từ event EmergencyStop trong receipt."""
        tx = self.send("reportIncident", robot_id, evidence, severity, Web3.to_checksum_address(operator))
        events = self.contract.events.EmergencyStop().process_receipt(tx.receipt, errors=DISCARD)
        return tx, int(events[0]["args"]["incidentId"])

    def unlock(self, robot_id: int) -> TxResult:
        return self.send("unlock", robot_id)

    def check_in(self, robot_id: int) -> TxResult:
        return self.send("checkIn", robot_id)

    def record_audit(self, robot_id: int, batch_id: int, passed: bool, report_hash: bytes) -> TxResult:
        return self.send("recordAudit", robot_id, batch_id, passed, report_hash)

    # ---------------- đọc ----------------
    def robot_info(self, robot_id: int) -> RobotInfo:
        device, state, registered, last_batch_id, last_to_seq, current_op = \
            self.contract.functions.getRobot(robot_id).call()
        return RobotInfo(registered, device, state, last_batch_id, last_to_seq, current_op)

    def get_batch(self, robot_id: int, batch_id: int) -> tuple[bytes, int, int, int]:
        """(merkleRoot, fromSeq, toSeq, anchoredAt) – merkleRoot = 32 byte 0 nếu không tồn tại."""
        root, from_seq, to_seq, at = self.contract.functions.getBatch(robot_id, batch_id).call()
        return bytes(root), from_seq, to_seq, at

    def verify_record(self, batch_id: int, record: tuple[int, ...], proof: list[bytes]) -> bool:
        return bool(self.contract.functions.verifyRecord(batch_id, record, proof).call())

    def leaf_hash(self, record: tuple[int, ...]) -> bytes:
        return bytes(self.contract.functions.leafHash(record).call())

    def has_role(self, role_name: str, account: str) -> bool:
        role = Web3.keccak(text=role_name) if role_name != "DEFAULT_ADMIN_ROLE" else bytes(32)
        return bool(self.contract.functions.hasRole(role, Web3.to_checksum_address(account)).call())

    def violation_count(self, operator: str) -> int:
        return int(self.contract.functions.violationCount(Web3.to_checksum_address(operator)).call())

    def block_number(self) -> int:
        return self.w3.eth.block_number

    def get_events(self, from_block: int, to_block: int, names: tuple[str, ...] = EVENT_NAMES) -> list[dict]:
        """Các event trong [from_block, to_block], sắp theo (block, logIndex).

        Chỉ 1 lệnh eth_getLogs (lọc theo địa chỉ contract) rồi tự giải mã theo topic0 – tiết kiệm quota
        của RPC công khai (Alchemy/Infura) so với gọi riêng từng loại event.
        """
        by_topic = {}
        for name in names:
            ev = getattr(self.contract.events, name)
            abi = next(i for i in self.abi if i.get("type") == "event" and i["name"] == name)
            sig = f"{name}({','.join(i['type'] for i in abi['inputs'])})"
            by_topic[Web3.keccak(text=sig)] = (name, ev())
        logs = self.w3.eth.get_logs({"address": self.address, "fromBlock": from_block, "toBlock": to_block})
        out = []
        for lg in logs:
            if not lg["topics"] or bytes(lg["topics"][0]) not in by_topic:
                continue
            name, ev = by_topic[bytes(lg["topics"][0])]
            d = ev.process_log(lg)
            out.append({"event": name, "args": dict(d["args"]), "block": d["blockNumber"],
                        "log_index": d["logIndex"], "tx_hash": Web3.to_hex(d["transactionHash"])})
        return sorted(out, key=lambda e: (e["block"], e["log_index"]))

    def deploy_block(self) -> int:
        """Block contract được deploy (tìm nhị phân trên eth_getCode, ~log2(chiều cao chuỗi) lệnh RPC)."""
        lo, hi = 0, self.block_number()
        while lo < hi:
            mid = (lo + hi) // 2
            if self.w3.eth.get_code(self.address, block_identifier=mid) in (b"", b"\x00"):
                lo = mid + 1
            else:
                hi = mid
        return lo
