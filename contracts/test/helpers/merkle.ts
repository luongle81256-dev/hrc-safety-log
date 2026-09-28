// Cây Merkle sorted-pair theo interface đã chốt (docs/INTERFACES.md mục A4).
// Bản Python (Phase 4) phải cho ra đúng cùng root/proof với file này.
import { AbiCoder, concat, keccak256 } from "ethers";

/** 1 dòng telemetry – đúng thứ tự trường của leaf */
export interface LogRecord {
  robotId: bigint;
  seq: bigint;
  tsMs: bigint;
  distanceMm: bigint;
  vRobotMmps: bigint;
  zone: bigint;
  estop: bigint;
}

const coder = AbiCoder.defaultAbiCoder();

/** leaf = keccak256(abi.encode(robot_id, seq, ts_ms, distance_mm, v_robot_mmps, zone, estop)) */
export function leafHash(r: LogRecord): string {
  return keccak256(
    coder.encode(
      Array(7).fill("uint256"),
      [r.robotId, r.seq, r.tsMs, r.distanceMm, r.vRobotMmps, r.zone, r.estop],
    ),
  );
}

/** parent = keccak256(min(a,b) ‖ max(a,b)) – giống OpenZeppelin Hashes.commutativeKeccak256 */
export function hashPair(a: string, b: string): string {
  return BigInt(a) < BigInt(b) ? keccak256(concat([a, b])) : keccak256(concat([b, a]));
}

/** Trả về các tầng của cây: layers[0] = leaves, tầng cuối = [root]. Node lẻ được đẩy thẳng lên. */
export function buildLayers(leaves: string[]): string[][] {
  if (leaves.length === 0) throw new Error("Batch rỗng không có Merkle root");
  const layers = [leaves];
  while (layers[layers.length - 1].length > 1) {
    const cur = layers[layers.length - 1];
    const next: string[] = [];
    for (let i = 0; i < cur.length; i += 2) {
      next.push(i + 1 < cur.length ? hashPair(cur[i], cur[i + 1]) : cur[i]);
    }
    layers.push(next);
  }
  return layers;
}

export function merkleRoot(layers: string[][]): string {
  return layers[layers.length - 1][0];
}

/** Proof cho leaf thứ `index`: bỏ qua tầng nào mà node không có anh em (node lẻ được đẩy lên) */
export function getProof(layers: string[][], index: number): string[] {
  const proof: string[] = [];
  for (let level = 0; level < layers.length - 1; level++) {
    const sibling = index ^ 1;
    if (sibling < layers[level].length) proof.push(layers[level][sibling]);
    index >>= 1;
  }
  return proof;
}

/** Sinh `n` bản ghi giả lập liên tiếp bắt đầu từ `fromSeq` */
export function makeRecords(robotId: bigint, fromSeq: bigint, n: number): LogRecord[] {
  return Array.from({ length: n }, (_, i) => ({
    robotId,
    seq: fromSeq + BigInt(i),
    tsMs: 1_790_000_000_000n + BigInt(i) * 100n,
    distanceMm: BigInt(400 - (i % 300)),
    vRobotMmps: 150n,
    zone: BigInt(i % 3),
    estop: i % 3 === 2 ? 1n : 0n,
  }));
}

/** Gom batch: trả về root, layers, dải seq */
export function buildBatch(records: LogRecord[]) {
  const layers = buildLayers(records.map(leafHash));
  return {
    root: merkleRoot(layers),
    layers,
    fromSeq: records[0].seq,
    toSeq: records[records.length - 1].seq,
  };
}
