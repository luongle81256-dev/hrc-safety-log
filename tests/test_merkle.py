"""Merkle: Python == TypeScript (test vector) == contract on-chain (Hardhat node, tự bỏ qua nếu không có)."""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from hrc_common.merkle import build_layers, get_proof, hash_pair, leaf_hash, merkle_root, verify_proof

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "merkle_vectors.json").read_text(encoding="utf-8"))


def _h(b: bytes) -> str:
    return "0x" + b.hex()


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: f"n={len(c['records'])}")
def test_python_matches_typescript(case):
    records = [tuple(int(v) for v in r) for r in case["records"]]
    leaves = [leaf_hash(r) for r in records]
    assert [_h(x) for x in leaves] == case["leaves"]
    layers = build_layers(leaves)
    assert _h(layers[-1][0]) == case["root"]
    for i in range(len(records)):
        assert [_h(p) for p in get_proof(layers, i)] == case["proofs"][i]


def test_verify_and_tamper():
    recs = [(1, s, 1_790_000_000_000 + s * 100, 400, 150, 0, 0) for s in range(1, 38)]
    layers = build_layers([leaf_hash(r) for r in recs])
    root = layers[-1][0]
    for i, r in enumerate(recs):
        assert verify_proof(leaf_hash(r), get_proof(layers, i), root)
    bad = (1, 5, recs[4][2], 401, 150, 0, 0)
    assert not verify_proof(leaf_hash(bad), get_proof(layers, 4), root)


def test_sorted_pair_commutative():
    a, b = leaf_hash((1,) * 7), leaf_hash((2,) * 7)
    assert hash_pair(a, b) == hash_pair(b, a)


def test_empty_and_bad_input():
    with pytest.raises(ValueError):
        merkle_root([])
    with pytest.raises(ValueError):
        leaf_hash((1, 2, 3))
    with pytest.raises(IndexError):
        get_proof(build_layers([leaf_hash((1,) * 7)]), 1)


# ---------------- on-chain: Python root/proof được contract chấp nhận ----------------
def _node():
    try:
        from web3 import Web3

        from hrc_backend.chain import rpc_url

        w3 = Web3(Web3.HTTPProvider(rpc_url(), request_kwargs={"timeout": 3}))
        return w3 if w3.is_connected() else None
    except Exception:
        return None


W3 = _node()


@pytest.fixture(scope="module")
def fresh_contract():
    """Deploy contract MỚI (không đụng bản của demo) bằng tài khoản đã mở khóa của Hardhat node."""
    from hrc_backend.chain import ARTIFACT_PATH

    art = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    admin, device = W3.eth.accounts[0], W3.eth.accounts[1]
    factory = W3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    rcpt = W3.eth.wait_for_transaction_receipt(factory.constructor(admin, 3).transact({"from": admin}))
    c = W3.eth.contract(address=rcpt["contractAddress"], abi=art["abi"])
    W3.eth.wait_for_transaction_receipt(c.functions.grantRole(W3.keccak(text="DEVICE_ROLE"), device).transact({"from": admin}))
    W3.eth.wait_for_transaction_receipt(c.functions.registerRobot(1, device).transact({"from": admin}))
    return c, device


@pytest.mark.chain
@pytest.mark.skipif(W3 is None, reason="không có Hardhat node")
def test_python_merkle_accepted_on_chain(fresh_contract):
    c, device = fresh_contract
    rng = random.Random(7)
    next_seq, batch_id = 1, 0
    for n in (1, 2, 3, 7, 64, 257):
        recs = [(1, next_seq + i, 1_790_000_000_000 + i * 100, rng.randint(0, 8190), rng.randint(0, 400),
                 rng.randint(0, 2), rng.randint(0, 1)) for i in range(n)]
        layers = build_layers([leaf_hash(r) for r in recs])
        batch_id += 1
        W3.eth.wait_for_transaction_receipt(c.functions.anchorLog(1, batch_id, layers[-1][0], recs[0][1], recs[-1][1])
                                            .transact({"from": device}))
        idx = range(n) if n <= 7 else rng.sample(range(n), 12)
        for i in idx:
            proof = get_proof(layers, i)
            assert bytes(c.functions.leafHash(recs[i]).call()) == leaf_hash(recs[i])
            assert c.functions.verifyRecord(batch_id, recs[i], proof).call() is True
            tampered = recs[i][:3] + (recs[i][3] + 1,) + recs[i][4:]
            assert c.functions.verifyRecord(batch_id, tampered, proof).call() is False
        next_seq += n
