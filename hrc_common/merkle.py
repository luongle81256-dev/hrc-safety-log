"""Cây Merkle sorted-pair – bản Python (docs/INTERFACES.md mục 7).

PHẢI cho CÙNG leaf/root/proof với:
    - contract HRCSafetyLog.leafHash + OpenZeppelin MerkleProof (on-chain)
    - contracts/test/helpers/merkle.ts (TypeScript)
tests/test_merkle.py đối chiếu cả 3.
"""
from __future__ import annotations

from collections.abc import Sequence

from eth_abi import encode
from eth_utils import keccak

from .protocol import Telemetry

LEAF_TYPES = ["uint256"] * 7


def leaf_hash(values: Sequence[int]) -> bytes:
    """leaf = keccak256(abi.encode(robot_id, seq, ts_ms, distance_mm, v_robot_mmps, zone, estop))"""
    if len(values) != 7:
        raise ValueError("leaf cần đúng 7 trường")
    return keccak(encode(LEAF_TYPES, list(values)))


def telemetry_leaf(t: Telemetry) -> bytes:
    return leaf_hash(t.values())


def hash_pair(a: bytes, b: bytes) -> bytes:
    """keccak256(min(a,b) ‖ max(a,b)) – giống Hashes.commutativeKeccak256 của OpenZeppelin."""
    return keccak(a + b) if a < b else keccak(b + a)


def build_layers(leaves: Sequence[bytes]) -> list[list[bytes]]:
    """layers[0] = leaves (theo thứ tự seq), tầng cuối = [root]. Node lẻ được đẩy thẳng lên (không nhân đôi)."""
    if not leaves:
        raise ValueError("Batch rỗng không có Merkle root")
    layers = [list(leaves)]
    while len(layers[-1]) > 1:
        cur = layers[-1]
        layers.append([hash_pair(cur[i], cur[i + 1]) if i + 1 < len(cur) else cur[i] for i in range(0, len(cur), 2)])
    return layers


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    return build_layers(leaves)[-1][0]


def get_proof(layers: list[list[bytes]], index: int) -> list[bytes]:
    """Proof cho leaf thứ `index`; bỏ qua tầng mà node không có anh em."""
    if not 0 <= index < len(layers[0]):
        raise IndexError(index)
    proof = []
    for level in layers[:-1]:
        sib = index ^ 1
        if sib < len(level):
            proof.append(level[sib])
        index >>= 1
    return proof


def verify_proof(leaf: bytes, proof: Sequence[bytes], root: bytes) -> bool:
    """Kiểm tra off-chain (cùng thuật toán với MerkleProof.verify)."""
    h = leaf
    for p in proof:
        h = hash_pair(h, p)
    return h == root
