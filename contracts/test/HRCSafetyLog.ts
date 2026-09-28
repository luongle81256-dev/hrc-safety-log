import { readFileSync } from "node:fs";
import { expect } from "chai";
import { network } from "hardhat";
import { anyValue } from "@nomicfoundation/hardhat-ethers-chai-matchers/withArgs";
import { ZeroAddress, ZeroHash, id, keccak256, toUtf8Bytes } from "ethers";
import { buildBatch, getProof, hashPair, leafHash, makeRecords, type LogRecord } from "./helpers/merkle.js";

const { ethers, networkHelpers } = await network.create();

// Tham số lấy từ config dùng chung (sinh bởi scripts/gen_config.py)
const HRC = JSON.parse(readFileSync(new URL("../hrc_params.json", import.meta.url), "utf8"));
const MAX_VIOLATIONS = BigInt(HRC.maxViolations);

const ROLE = {
  ADMIN: ZeroHash,
  DEVICE: id("DEVICE_ROLE"),
  OPERATOR: id("OPERATOR_ROLE"),
  SUPERVISOR: id("SUPERVISOR_ROLE"),
  AUDITOR: id("AUDITOR_ROLE"),
};
const ACTIVE = 0n;
const LOCKED = 1n;
const R1 = 1n; // robot chính
const R2 = 2n; // robot thứ 2, gắn ví device2
const EVIDENCE = keccak256(toUtf8Bytes("evidence-1"));
const ROOT_A = keccak256(toUtf8Bytes("root-A"));
const ROOT_B = keccak256(toUtf8Bytes("root-B"));

async function deployFixture() {
  const [admin, device, device2, supervisor, operator, operator2, auditor, outsider] =
    await ethers.getSigners();
  const log = await ethers.deployContract("HRCSafetyLog", [admin.address, MAX_VIOLATIONS]);

  await log.grantRole(ROLE.DEVICE, device.address);
  await log.grantRole(ROLE.DEVICE, device2.address);
  await log.grantRole(ROLE.SUPERVISOR, supervisor.address);
  await log.grantRole(ROLE.OPERATOR, operator.address);
  await log.grantRole(ROLE.OPERATOR, operator2.address);
  await log.grantRole(ROLE.AUDITOR, auditor.address);
  await log.registerRobot(R1, device.address);
  await log.registerRobot(R2, device2.address);

  return { log, admin, device, device2, supervisor, operator, operator2, auditor, outsider };
}

describe("HRCSafetyLog", function () {
  // ---------------------------------------------------------------------------
  describe("Khởi tạo", function () {
    it("admin có DEFAULT_ADMIN_ROLE, MAX_VIOLATIONS đúng config", async function () {
      const { log, admin } = await networkHelpers.loadFixture(deployFixture);
      expect(await log.hasRole(ROLE.ADMIN, admin.address)).to.equal(true);
      expect(await log.MAX_VIOLATIONS()).to.equal(MAX_VIOLATIONS);
      expect(await log.incidentCount()).to.equal(0n);
    });

    it("từ chối admin = 0 và maxViolations = 0", async function () {
      const [admin] = await ethers.getSigners();
      const factory = await ethers.getContractFactory("HRCSafetyLog");
      await expect(factory.deploy(ZeroAddress, 3n)).to.be.revertedWithCustomError(factory, "ZeroAddress");
      await expect(factory.deploy(admin.address, 0n)).to.be.revertedWithCustomError(
        factory,
        "InvalidMaxViolations",
      );
    });
  });

  // ---------------------------------------------------------------------------
  describe("Đăng ký robot & phân quyền", function () {
    it("robot mới ở trạng thái ACTIVE, gắn đúng ví device", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      const r = await log.getRobot(R1);
      expect(r.registered).to.equal(true);
      expect(r.device).to.equal(device.address);
      expect(r.state).to.equal(ACTIVE);
      expect(r.lastBatchId).to.equal(0n);
    });

    it("chỉ admin được đăng ký robot; không đăng ký trùng; không nhận ví 0", async function () {
      const { log, outsider, device } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(outsider).registerRobot(9n, device.address))
        .to.be.revertedWithCustomError(log, "AccessControlUnauthorizedAccount")
        .withArgs(outsider.address, ROLE.ADMIN);
      await expect(log.registerRobot(R1, device.address))
        .to.be.revertedWithCustomError(log, "RobotAlreadyRegistered")
        .withArgs(R1);
      await expect(log.registerRobot(9n, ZeroAddress)).to.be.revertedWithCustomError(log, "ZeroAddress");
      await expect(log.registerRobot(9n, device.address)).to.emit(log, "RobotRegistered").withArgs(9n, device.address);
    });

    it("xoay khóa: setRobotDevice đổi ví, ví cũ mất quyền ghi cho robot", async function () {
      const { log, device, device2, outsider } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(outsider).setRobotDevice(R1, device2.address)).to.be.revertedWithCustomError(
        log,
        "AccessControlUnauthorizedAccount",
      );
      await expect(log.setRobotDevice(99n, device2.address))
        .to.be.revertedWithCustomError(log, "RobotNotRegistered")
        .withArgs(99n);
      await expect(log.setRobotDevice(R1, ZeroAddress)).to.be.revertedWithCustomError(log, "ZeroAddress");
      await expect(log.setRobotDevice(R1, device2.address))
        .to.emit(log, "RobotDeviceChanged")
        .withArgs(R1, device.address, device2.address);
      await expect(log.connect(device).anchorLog(R1, 1n, ROOT_A, 1n, 10n))
        .to.be.revertedWithCustomError(log, "NotRobotDevice")
        .withArgs(R1, device.address);
      await expect(log.connect(device2).anchorLog(R1, 1n, ROOT_A, 1n, 10n)).to.emit(log, "LogAnchored");
    });

    it("các hàm đặc quyền từ chối người không có role", async function () {
      const { log, outsider, operator } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(outsider).anchorLog(R1, 1n, ROOT_A, 1n, 10n))
        .to.be.revertedWithCustomError(log, "AccessControlUnauthorizedAccount")
        .withArgs(outsider.address, ROLE.DEVICE);
      await expect(log.connect(operator).reportIncident(R1, EVIDENCE, 2, ZeroAddress))
        .to.be.revertedWithCustomError(log, "AccessControlUnauthorizedAccount")
        .withArgs(operator.address, ROLE.DEVICE);
      await expect(log.connect(outsider).checkIn(R1))
        .to.be.revertedWithCustomError(log, "AccessControlUnauthorizedAccount")
        .withArgs(outsider.address, ROLE.OPERATOR);
      await expect(log.connect(outsider).recordAudit(R1, 1n, true, ZeroHash))
        .to.be.revertedWithCustomError(log, "AccessControlUnauthorizedAccount")
        .withArgs(outsider.address, ROLE.AUDITOR);
      await expect(log.connect(outsider).resetViolations(operator.address))
        .to.be.revertedWithCustomError(log, "AccessControlUnauthorizedAccount")
        .withArgs(outsider.address, ROLE.ADMIN);
    });

    it("DEVICE của robot này không ghi được cho robot khác", async function () {
      const { log, device, device2 } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(device2).anchorLog(R1, 1n, ROOT_A, 1n, 10n))
        .to.be.revertedWithCustomError(log, "NotRobotDevice")
        .withArgs(R1, device2.address);
      await expect(log.connect(device).reportIncident(R2, EVIDENCE, 2, ZeroAddress))
        .to.be.revertedWithCustomError(log, "NotRobotDevice")
        .withArgs(R2, device.address);
    });

    it("robot chưa đăng ký bị từ chối", async function () {
      const { log, device, supervisor } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(device).anchorLog(77n, 1n, ROOT_A, 1n, 10n))
        .to.be.revertedWithCustomError(log, "RobotNotRegistered")
        .withArgs(77n);
      await expect(log.connect(supervisor).unlock(77n))
        .to.be.revertedWithCustomError(log, "RobotNotRegistered")
        .withArgs(77n);
    });

    it("thu hồi DEVICE_ROLE => ví bị lộ không ghi được nữa", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await log.revokeRole(ROLE.DEVICE, device.address);
      await expect(log.connect(device).anchorLog(R1, 1n, ROOT_A, 1n, 10n))
        .to.be.revertedWithCustomError(log, "AccessControlUnauthorizedAccount")
        .withArgs(device.address, ROLE.DEVICE);
    });
  });

  // ---------------------------------------------------------------------------
  describe("anchorLog – chống replay / chồng lấn batch", function () {
    it("ghi batch hợp lệ, emit LogAnchored, lưu đúng batch", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      const tx = log.connect(device).anchorLog(R1, 1n, ROOT_A, 1n, 256n);
      await expect(tx).to.emit(log, "LogAnchored").withArgs(R1, 1n, ROOT_A, 1n, 256n, anyValue);
      const b = await log.getBatch(R1, 1n);
      expect(b.merkleRoot).to.equal(ROOT_A);
      expect(b.fromSeq).to.equal(1n);
      expect(b.toSeq).to.equal(256n);
      const block = await ethers.provider.getBlock("latest");
      expect(b.anchoredAt).to.equal(BigInt(block!.timestamp));
      const r = await log.getRobot(R1);
      expect(r.lastBatchId).to.equal(1n);
      expect(r.lastToSeq).to.equal(256n);
    });

    it("batch đầu tiên phải có batchId = 1", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(device).anchorLog(R1, 0n, ROOT_A, 1n, 10n))
        .to.be.revertedWithCustomError(log, "InvalidBatchId")
        .withArgs(R1, 1n, 0n);
      await expect(log.connect(device).anchorLog(R1, 2n, ROOT_A, 1n, 10n))
        .to.be.revertedWithCustomError(log, "InvalidBatchId")
        .withArgs(R1, 1n, 2n);
    });

    it("REPLAY: gửi lại đúng batch cũ bị từ chối", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(device).anchorLog(R1, 1n, ROOT_A, 1n, 10n);
      await expect(log.connect(device).anchorLog(R1, 1n, ROOT_A, 1n, 10n))
        .to.be.revertedWithCustomError(log, "InvalidBatchId")
        .withArgs(R1, 2n, 1n);
    });

    it("không cho nhảy cóc batchId", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(device).anchorLog(R1, 1n, ROOT_A, 1n, 10n);
      await expect(log.connect(device).anchorLog(R1, 3n, ROOT_B, 11n, 20n))
        .to.be.revertedWithCustomError(log, "InvalidBatchId")
        .withArgs(R1, 2n, 3n);
    });

    it("CHỒNG LẤN: fromSeq <= toSeq của batch trước bị từ chối", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(device).anchorLog(R1, 1n, ROOT_A, 1n, 10n);
      await expect(log.connect(device).anchorLog(R1, 2n, ROOT_B, 10n, 20n))
        .to.be.revertedWithCustomError(log, "SeqOverlap")
        .withArgs(R1, 10n, 10n);
      await expect(log.connect(device).anchorLog(R1, 2n, ROOT_B, 5n, 20n))
        .to.be.revertedWithCustomError(log, "SeqOverlap")
        .withArgs(R1, 10n, 5n);
      // fromSeq = toSeq_trước + 1 là hợp lệ; có khoảng trống seq (mất gói QoS 0) cũng hợp lệ
      await expect(log.connect(device).anchorLog(R1, 2n, ROOT_B, 11n, 20n)).to.emit(log, "LogAnchored");
      await expect(log.connect(device).anchorLog(R1, 3n, ROOT_A, 25n, 30n)).to.emit(log, "LogAnchored");
    });

    it("từ chối root = 0 và dải seq ngược", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(device).anchorLog(R1, 1n, ZeroHash, 1n, 10n)).to.be.revertedWithCustomError(
        log,
        "ZeroMerkleRoot",
      );
      await expect(log.connect(device).anchorLog(R1, 1n, ROOT_A, 11n, 10n))
        .to.be.revertedWithCustomError(log, "InvalidSeqRange")
        .withArgs(11n, 10n);
      // batch 1 bản ghi (fromSeq == toSeq) là hợp lệ
      await expect(log.connect(device).anchorLog(R1, 1n, ROOT_A, 5n, 5n)).to.emit(log, "LogAnchored");
    });

    it("bộ đếm batch độc lập giữa các robot", async function () {
      const { log, device, device2 } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(device).anchorLog(R1, 1n, ROOT_A, 1n, 10n);
      await expect(log.connect(device2).anchorLog(R2, 1n, ROOT_B, 1n, 10n)).to.emit(log, "LogAnchored");
    });

    it("vẫn ghi log khi robot LOCKED (hộp đen không ngừng ghi)", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(device).reportIncident(R1, EVIDENCE, 2, ZeroAddress);
      expect(await log.robotState(R1)).to.equal(LOCKED);
      await expect(log.connect(device).anchorLog(R1, 1n, ROOT_A, 1n, 10n)).to.emit(log, "LogAnchored");
    });
  });

  // ---------------------------------------------------------------------------
  describe("Luồng ACTIVE → LOCKED → ACTIVE", function () {
    it("reportIncident: LOCKED, emit EmergencyStop, lưu sự cố", async function () {
      const { log, device, operator } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(device).reportIncident(R1, EVIDENCE, 2, operator.address))
        .to.emit(log, "EmergencyStop")
        .withArgs(R1, 1n, operator.address, EVIDENCE, 2, anyValue)
        .and.to.emit(log, "ViolationRecorded")
        .withArgs(operator.address, 1n);
      expect(await log.robotState(R1)).to.equal(LOCKED);
      expect(await log.incidentCount()).to.equal(1n);
      expect(await log.violationCount(operator.address)).to.equal(1n);
      const inc = await log.getIncident(1n);
      expect(inc.evidenceHash).to.equal(EVIDENCE);
      expect(inc.robotId).to.equal(R1);
      expect(inc.operator).to.equal(operator.address);
      expect(inc.severity).to.equal(2n);
    });

    it("reportIncident trả về incidentId tăng dần", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      expect(await log.connect(device).reportIncident.staticCall(R1, EVIDENCE, 1, ZeroAddress)).to.equal(1n);
      await log.connect(device).reportIncident(R1, EVIDENCE, 1, ZeroAddress);
      expect(await log.connect(device).reportIncident.staticCall(R1, EVIDENCE, 1, ZeroAddress)).to.equal(2n);
    });

    it("operator = 0: không quy lỗi cho ai, không tăng vi phạm", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(device).reportIncident(R1, EVIDENCE, 3, ZeroAddress)).not.to.emit(
        log,
        "ViolationRecorded",
      );
      expect(await log.violationCount(ZeroAddress)).to.equal(0n);
    });

    it("từ chối evidenceHash = 0 và severity ngoài [1,3]", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(device).reportIncident(R1, ZeroHash, 2, ZeroAddress)).to.be.revertedWithCustomError(
        log,
        "ZeroEvidenceHash",
      );
      for (const s of [0, 4, 255]) {
        await expect(log.connect(device).reportIncident(R1, EVIDENCE, s, ZeroAddress))
          .to.be.revertedWithCustomError(log, "InvalidSeverity")
          .withArgs(s);
      }
    });

    it("chỉ SUPERVISOR unlock được; emit Unlocked(robotId, supervisor, timestamp)", async function () {
      const { log, device, supervisor, operator, admin } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(device).reportIncident(R1, EVIDENCE, 2, ZeroAddress);
      for (const who of [operator, device, admin]) {
        await expect(log.connect(who).unlock(R1))
          .to.be.revertedWithCustomError(log, "AccessControlUnauthorizedAccount")
          .withArgs(who.address, ROLE.SUPERVISOR);
      }
      const tx = await log.connect(supervisor).unlock(R1);
      const block = await ethers.provider.getBlock(tx.blockNumber!);
      await expect(tx).to.emit(log, "Unlocked").withArgs(R1, supervisor.address, BigInt(block!.timestamp));
      expect(await log.robotState(R1)).to.equal(ACTIVE);
    });

    it("unlock khi robot đang ACTIVE bị từ chối", async function () {
      const { log, supervisor } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(supervisor).unlock(R1))
        .to.be.revertedWithCustomError(log, "RobotNotLocked")
        .withArgs(R1);
    });

    it("chu trình lặp lại được: LOCKED → ACTIVE → LOCKED → ACTIVE", async function () {
      const { log, device, supervisor } = await networkHelpers.loadFixture(deployFixture);
      for (let i = 0; i < 2; i++) {
        await log.connect(device).reportIncident(R1, EVIDENCE, 2, ZeroAddress);
        expect(await log.robotState(R1)).to.equal(LOCKED);
        await log.connect(supervisor).unlock(R1);
        expect(await log.robotState(R1)).to.equal(ACTIVE);
      }
    });

    it("sự cố thứ 2 khi đang LOCKED vẫn được ghi, robot giữ LOCKED", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(device).reportIncident(R1, EVIDENCE, 2, ZeroAddress);
      await expect(log.connect(device).reportIncident(R1, EVIDENCE, 3, ZeroAddress)).to.emit(log, "EmergencyStop");
      expect(await log.robotState(R1)).to.equal(LOCKED);
      expect(await log.incidentCount()).to.equal(2n);
    });

    it("khóa robot này không ảnh hưởng robot khác", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(device).reportIncident(R1, EVIDENCE, 2, ZeroAddress);
      expect(await log.robotState(R2)).to.equal(ACTIVE);
    });
  });

  // ---------------------------------------------------------------------------
  describe("Operator: check-in / vi phạm / bị khóa", function () {
    it("check-in / check-out bình thường", async function () {
      const { log, operator } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(operator).checkIn(R1)).to.emit(log, "OperatorCheckedIn").withArgs(R1, operator.address);
      expect((await log.getRobot(R1)).currentOperator).to.equal(operator.address);
      await expect(log.connect(operator).checkIn(R1)).to.emit(log, "OperatorCheckedIn"); // check-in lại: idempotent
      await expect(log.connect(operator).checkOut(R1)).to.emit(log, "OperatorCheckedOut").withArgs(R1, operator.address);
      expect((await log.getRobot(R1)).currentOperator).to.equal(ZeroAddress);
    });

    it("không check-in được robot đang có người khác hoặc đang LOCKED", async function () {
      const { log, device2, operator, operator2 } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(operator).checkIn(R1);
      await expect(log.connect(operator2).checkIn(R1))
        .to.be.revertedWithCustomError(log, "RobotBusy")
        .withArgs(R1, operator.address);
      await expect(log.connect(operator2).checkOut(R1))
        .to.be.revertedWithCustomError(log, "NotCurrentOperator")
        .withArgs(R1, operator2.address);
      await log.connect(device2).reportIncident(R2, EVIDENCE, 2, ZeroAddress);
      await expect(log.connect(operator2).checkIn(R2))
        .to.be.revertedWithCustomError(log, "RobotIsLocked")
        .withArgs(R2);
    });

    it("unlock xóa operator hiện tại, buộc check-in lại", async function () {
      const { log, device, supervisor, operator } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(operator).checkIn(R1);
      await log.connect(device).reportIncident(R1, EVIDENCE, 2, operator.address);
      await expect(log.connect(supervisor).unlock(R1))
        .to.emit(log, "OperatorCheckedOut")
        .withArgs(R1, operator.address);
      expect((await log.getRobot(R1)).currentOperator).to.equal(ZeroAddress);
    });

    it(`vi phạm ${MAX_VIOLATIONS} lần => thu hồi OPERATOR_ROLE, emit OperatorBlocked đúng 1 lần`, async function () {
      const { log, device, supervisor, operator } = await networkHelpers.loadFixture(deployFixture);
      for (let i = 1n; i < MAX_VIOLATIONS; i++) {
        await expect(log.connect(device).reportIncident(R1, EVIDENCE, 2, operator.address)).not.to.emit(
          log,
          "OperatorBlocked",
        );
        await log.connect(supervisor).unlock(R1);
        expect(await log.hasRole(ROLE.OPERATOR, operator.address)).to.equal(true);
      }
      await expect(log.connect(device).reportIncident(R1, EVIDENCE, 2, operator.address))
        .to.emit(log, "OperatorBlocked")
        .withArgs(operator.address, MAX_VIOLATIONS)
        .and.to.emit(log, "RoleRevoked")
        .withArgs(ROLE.OPERATOR, operator.address, device.address);
      expect(await log.hasRole(ROLE.OPERATOR, operator.address)).to.equal(false);

      // vi phạm tiếp: vẫn đếm nhưng không emit OperatorBlocked lặp lại
      await expect(log.connect(device).reportIncident(R1, EVIDENCE, 2, operator.address)).not.to.emit(
        log,
        "OperatorBlocked",
      );
      expect(await log.violationCount(operator.address)).to.equal(MAX_VIOLATIONS + 1n);
    });

    it("operator bị khóa: không check-in được, admin không cấp lại được nếu chưa reset", async function () {
      const { log, device, supervisor, operator } = await networkHelpers.loadFixture(deployFixture);
      for (let i = 0n; i < MAX_VIOLATIONS; i++) {
        await log.connect(device).reportIncident(R1, EVIDENCE, 2, operator.address);
      }
      await log.connect(supervisor).unlock(R1);
      await expect(log.connect(operator).checkIn(R1))
        .to.be.revertedWithCustomError(log, "AccessControlUnauthorizedAccount")
        .withArgs(operator.address, ROLE.OPERATOR);
      await expect(log.grantRole(ROLE.OPERATOR, operator.address))
        .to.be.revertedWithCustomError(log, "OperatorIsBlocked")
        .withArgs(operator.address, MAX_VIOLATIONS);

      // quy trình ân xá có kiểm soát: reset -> cấp lại -> check-in được
      await expect(log.resetViolations(operator.address)).to.emit(log, "ViolationsReset");
      await log.grantRole(ROLE.OPERATOR, operator.address);
      await expect(log.connect(operator).checkIn(R1)).to.emit(log, "OperatorCheckedIn");
    });

    it("vi phạm được đếm theo người, không theo robot", async function () {
      const { log, device, device2, operator, operator2 } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(device).reportIncident(R1, EVIDENCE, 2, operator.address);
      await log.connect(device2).reportIncident(R2, EVIDENCE, 2, operator.address);
      expect(await log.violationCount(operator.address)).to.equal(2n);
      expect(await log.violationCount(operator2.address)).to.equal(0n);
    });
  });

  // ---------------------------------------------------------------------------
  describe("Merkle: verifyLeaf / verifyRecord / leafHash", function () {
    it("leafHash on-chain == leafHash TypeScript", async function () {
      const { log } = await networkHelpers.loadFixture(deployFixture);
      for (const rec of makeRecords(R1, 1n, 5)) {
        expect(await log.leafHash(rec)).to.equal(leafHash(rec));
      }
    });

    for (const n of [1, 2, 3, 7, 256]) {
      it(`mọi bản ghi trong batch ${n} leaf đều verify đúng`, async function () {
        const { log, device } = await networkHelpers.loadFixture(deployFixture);
        const records = makeRecords(R1, 1n, n);
        const batch = buildBatch(records);
        await log.connect(device).anchorLog(R1, 1n, batch.root, batch.fromSeq, batch.toSeq);
        // batch lớn: kiểm tra mẫu để test nhanh
        const idx = n <= 8 ? records.map((_, i) => i) : [0, 1, 127, 128, n - 2, n - 1];
        for (const i of idx) {
          const proof = getProof(batch.layers, i);
          expect(await log.verifyRecord(1n, records[i], proof)).to.equal(true);
          expect(await log.verifyLeaf(R1, 1n, leafHash(records[i]), proof)).to.equal(true);
        }
      });
    }

    it("TAMPER: sửa 1 trường của bản ghi => verify thất bại", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      const records = makeRecords(R1, 100n, 16);
      const batch = buildBatch(records);
      await log.connect(device).anchorLog(R1, 1n, batch.root, batch.fromSeq, batch.toSeq);
      const proof = getProof(batch.layers, 5);
      const fields: (keyof LogRecord)[] = ["seq", "tsMs", "distanceMm", "vRobotMmps", "zone", "estop"];
      for (const f of fields) {
        const tampered = { ...records[5], [f]: records[5][f] + 1n };
        expect(await log.verifyRecord(1n, tampered, proof), `sửa ${f}`).to.equal(false);
      }
      // đổi robotId => tra sang batch của robot khác => false
      expect(await log.verifyRecord(1n, { ...records[5], robotId: R2 }, proof)).to.equal(false);
    });

    it("proof sai / batch sai / batch không tồn tại => false", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      const recA = makeRecords(R1, 1n, 8);
      const recB = makeRecords(R1, 9n, 8);
      const a = buildBatch(recA);
      const b = buildBatch(recB);
      await log.connect(device).anchorLog(R1, 1n, a.root, a.fromSeq, a.toSeq);
      await log.connect(device).anchorLog(R1, 2n, b.root, b.fromSeq, b.toSeq);

      expect(await log.verifyRecord(1n, recA[0], getProof(a.layers, 1))).to.equal(false); // proof của leaf khác
      expect(await log.verifyRecord(2n, recA[0], getProof(a.layers, 0))).to.equal(false); // sai batch
      expect(await log.verifyRecord(3n, recA[0], getProof(a.layers, 0))).to.equal(false); // batch chưa có
      expect(await log.verifyLeaf(R1, 3n, leafHash(recA[0]), getProof(a.layers, 0))).to.equal(false);
      expect(await log.verifyLeaf(R2, 1n, leafHash(recA[0]), getProof(a.layers, 0))).to.equal(false);
    });

    it("verifyRecord từ chối seq nằm ngoài dải của batch", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      const records = makeRecords(R1, 50n, 4);
      const batch = buildBatch(records);
      // cố ý neo dải seq hẹp hơn nội dung cây => bản ghi seq=53 phải bị từ chối
      await log.connect(device).anchorLog(R1, 1n, batch.root, 50n, 52n);
      expect(await log.verifyRecord(1n, records[0], getProof(batch.layers, 0))).to.equal(true);
      expect(await log.verifyRecord(1n, records[3], getProof(batch.layers, 3))).to.equal(false);
    });

    it("SECOND-PREIMAGE: verifyLeaf bị lừa bởi node trung gian, verifyRecord thì không", async function () {
      const { log, device } = await networkHelpers.loadFixture(deployFixture);
      const records = makeRecords(R1, 1n, 4);
      const batch = buildBatch(records);
      await log.connect(device).anchorLog(R1, 1n, batch.root, batch.fromSeq, batch.toSeq);

      // node trung gian = hash(leaf0, leaf1), proof chỉ còn node anh em ở tầng 1
      const internalNode = hashPair(batch.layers[0][0], batch.layers[0][1]);
      const shortProof = [batch.layers[1][1]];
      expect(await log.verifyLeaf(R1, 1n, internalNode, shortProof)).to.equal(true); // rủi ro đã biết

      // Kẻ tấn công không thể tạo LogRecord có leafHash == internalNode (cần tìm tiền ảnh keccak256)
      // => mọi bản ghi giả kèm shortProof đều bị verifyRecord từ chối
      const fake = { ...records[0], distanceMm: 999n };
      expect(await log.verifyRecord(1n, fake, shortProof)).to.equal(false);
    });
  });

  // ---------------------------------------------------------------------------
  describe("AUDITOR: recordAudit", function () {
    it("ghi kết quả đối soát cho batch tồn tại", async function () {
      const { log, device, auditor } = await networkHelpers.loadFixture(deployFixture);
      await log.connect(device).anchorLog(R1, 1n, ROOT_A, 1n, 10n);
      const report = keccak256(toUtf8Bytes("report"));
      await expect(log.connect(auditor).recordAudit(R1, 1n, false, report))
        .to.emit(log, "AuditRecorded")
        .withArgs(R1, 1n, auditor.address, false, report, anyValue);
    });

    it("batch không tồn tại bị từ chối", async function () {
      const { log, auditor } = await networkHelpers.loadFixture(deployFixture);
      await expect(log.connect(auditor).recordAudit(R1, 1n, true, ZeroHash))
        .to.be.revertedWithCustomError(log, "BatchNotFound")
        .withArgs(R1, 1n);
    });
  });
});
