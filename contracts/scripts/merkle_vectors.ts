// Xuất test vector Merkle từ bản TypeScript để test Python đối chiếu (tests/fixtures/merkle_vectors.json).
// Chạy: npx hardhat run scripts/merkle_vectors.ts > ../tests/fixtures/merkle_vectors.json
import { buildBatch, getProof, leafHash, makeRecords } from "../test/helpers/merkle.js";

const cases = [1, 2, 3, 5, 8, 13].map((n) => {
  const records = makeRecords(1n, 100n, n);
  const batch = buildBatch(records);
  return {
    records: records.map((r) => [r.robotId, r.seq, r.tsMs, r.distanceMm, r.vRobotMmps, r.zone, r.estop].map(String)),
    leaves: records.map(leafHash),
    root: batch.root,
    proofs: records.map((_, i) => getProof(batch.layers, i)),
  };
});
console.log(JSON.stringify({ source: "contracts/test/helpers/merkle.ts", cases }, null, 2));
