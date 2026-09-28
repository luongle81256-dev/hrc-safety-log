// Bảng gas các hàm chính + so sánh chi phí "neo theo batch" và "ghi từng dòng" trong 1 ca 8 giờ.
// Chạy: npx hardhat test mocha test/GasReport.ts
// Giá gas là GIẢ ĐỊNH (đặt qua biến môi trường GAS_PRICE_GWEI, mặc định 1 gwei) – chỉ để minh họa.
import { readFileSync } from "node:fs";
import { expect } from "chai";
import { network } from "hardhat";
import { ZeroAddress, formatEther, id, keccak256, parseUnits, toUtf8Bytes } from "ethers";
import type { ContractTransactionResponse } from "ethers";
import { buildBatch, getProof, makeRecords } from "./helpers/merkle.js";

const { ethers } = await network.create();

const HRC = JSON.parse(readFileSync(new URL("../hrc_params.json", import.meta.url), "utf8"));
const GAS_PRICE_GWEI = process.env.GAS_PRICE_GWEI ?? "1";

async function gasOf(p: Promise<ContractTransactionResponse>): Promise<bigint> {
  const receipt = await (await p).wait();
  return receipt!.gasUsed;
}

describe("Gas report", function () {
  it("in bảng gas + chi phí 1 ca 8 giờ", async function () {
    const [admin, device, supervisor, operator, auditor] = await ethers.getSigners();
    const R1 = 1n;
    const EVIDENCE = keccak256(toUtf8Bytes("evidence"));
    const rows: [string, bigint][] = [];

    const log = await ethers.deployContract("HRCSafetyLog", [admin.address, BigInt(HRC.maxViolations)]);
    rows.push(["deploy HRCSafetyLog", (await log.deploymentTransaction()!.wait())!.gasUsed]);
    rows.push(["grantRole (DEVICE)", await gasOf(log.grantRole(id("DEVICE_ROLE"), device.address))]);
    await log.grantRole(id("SUPERVISOR_ROLE"), supervisor.address);
    await log.grantRole(id("OPERATOR_ROLE"), operator.address);
    await log.grantRole(id("AUDITOR_ROLE"), auditor.address);
    rows.push(["registerRobot", await gasOf(log.registerRobot(R1, device.address))]);

    // Batch đúng kích thước cấu hình (256 bản ghi)
    const n = HRC.batchMaxRecords as number;
    const recs1 = makeRecords(R1, 1n, n);
    const b1 = buildBatch(recs1);
    rows.push([`anchorLog batch #1 (${n} bản ghi, lần đầu)`, await gasOf(log.connect(device).anchorLog(R1, 1n, b1.root, b1.fromSeq, b1.toSeq))]);
    const b2 = buildBatch(makeRecords(R1, BigInt(n) + 1n, n));
    const gasBatch = await gasOf(log.connect(device).anchorLog(R1, 2n, b2.root, b2.fromSeq, b2.toSeq));
    rows.push([`anchorLog batch #2 (${n} bản ghi, ổn định)`, gasBatch]);
    // "Ghi từng dòng": mỗi bản ghi 1 lần anchorLog (fromSeq == toSeq)
    const single = buildBatch(makeRecords(R1, 2n * BigInt(n) + 1n, 1));
    const gasSingle = await gasOf(log.connect(device).anchorLog(R1, 3n, single.root, single.fromSeq, single.toSeq));
    rows.push(["anchorLog 1 bản ghi (ghi từng dòng)", gasSingle]);

    rows.push(["checkIn", await gasOf(log.connect(operator).checkIn(R1))]);
    rows.push(["reportIncident (không quy lỗi)", await gasOf(log.connect(device).reportIncident(R1, EVIDENCE, 2, ZeroAddress))]);
    rows.push(["unlock (+ xóa operator)", await gasOf(log.connect(supervisor).unlock(R1))]);
    rows.push(["reportIncident (vi phạm lần 1)", await gasOf(log.connect(device).reportIncident(R1, EVIDENCE, 2, operator.address))]);
    await log.connect(supervisor).unlock(R1);
    await log.connect(device).reportIncident(R1, EVIDENCE, 2, operator.address);
    await log.connect(supervisor).unlock(R1);
    rows.push([`reportIncident (vi phạm lần ${HRC.maxViolations} => khóa operator)`, await gasOf(log.connect(device).reportIncident(R1, EVIDENCE, 2, operator.address))]);
    rows.push(["unlock", await gasOf(log.connect(supervisor).unlock(R1))]);
    rows.push(["recordAudit", await gasOf(log.connect(auditor).recordAudit(R1, 1n, true, EVIDENCE))]);
    const proof = getProof(b1.layers, 7);
    expect(await log.verifyRecord(1n, recs1[7], proof)).to.equal(true);
    rows.push([`verifyRecord (view, proof ${proof.length} phần tử)*`, await log.verifyRecord.estimateGas(1n, recs1[7], proof)]);

    // ---- In bảng ----
    const line = "-".repeat(66);
    console.log(`\n${line}\n  BẢNG GAS – HRCSafetyLog (solc 0.8.28, optimizer 200 runs)\n${line}`);
    for (const [name, gas] of rows) console.log(`  ${name.padEnd(52)}${gas.toLocaleString("en-US").padStart(12)}`);
    console.log("  * view: miễn phí khi gọi off-chain; số liệu là gas nếu gọi trong 1 giao dịch");

    // ---- So sánh 1 ca 8 giờ ----
    const recordsPerShift = BigInt((8 * 3600 * 1000) / HRC.telemetryPeriodMs);
    const batchesPerShift = (recordsPerShift + BigInt(n) - 1n) / BigInt(n);
    const gasPrice = parseUnits(GAS_PRICE_GWEI, "gwei");
    const totalBatch = batchesPerShift * gasBatch;
    const totalSingle = recordsPerShift * gasSingle;
    console.log(`${line}\n  1 CA 8 GIỜ @ ${1000 / HRC.telemetryPeriodMs} Hz = ${recordsPerShift.toLocaleString("en-US")} bản ghi, giá gas giả định ${GAS_PRICE_GWEI} gwei\n${line}`);
    console.log(`  Neo theo batch ${n}: ${batchesPerShift} tx  ${totalBatch.toLocaleString("en-US").padStart(16)} gas  = ${formatEther(totalBatch * gasPrice)} ETH`);
    console.log(`  Ghi từng dòng     : ${recordsPerShift} tx ${totalSingle.toLocaleString("en-US").padStart(16)} gas  = ${formatEther(totalSingle * gasPrice)} ETH`);
    console.log(`  => Batch rẻ hơn ~${(totalSingle / totalBatch).toString()} lần; ghi từng dòng cần ${1000 / HRC.telemetryPeriodMs} tx/giây (Sepolia ~12 s/khối => không khả thi)`);
    console.log(line);

    expect(totalBatch < totalSingle).to.equal(true);
  });
});
