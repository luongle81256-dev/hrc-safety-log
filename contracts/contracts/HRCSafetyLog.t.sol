// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {HRCSafetyLog} from "./HRCSafetyLog.sol";

/// @notice Fuzz test (tương thích Foundry, chạy bằng `npx hardhat test solidity`):
///         thử ngẫu nhiên hàng trăm bộ tham số cho các luật chống replay/chồng lấn và Merkle.
contract HRCSafetyLogFuzzTest is Test {
    HRCSafetyLog internal hrc;
    address internal constant DEVICE = address(0xD1CE);
    uint256 internal constant ROBOT = 1;

    function setUp() public {
        hrc = new HRCSafetyLog(address(this), 3);
        hrc.grantRole(hrc.DEVICE_ROLE(), DEVICE);
        hrc.registerRobot(ROBOT, DEVICE);
    }

    /// Batch 2 có fromSeq <= toSeq của batch 1 => LUÔN bị từ chối
    function testFuzz_OverlapAlwaysReverts(uint64 from1, uint64 len1, uint64 from2, uint64 len2) public {
        from1 = uint64(bound(from1, 0, type(uint64).max / 2));
        uint64 to1 = from1 + uint64(bound(len1, 0, 1_000_000));
        from2 = uint64(bound(from2, 0, to1));
        uint64 to2 = from2 + uint64(bound(len2, 0, 1_000_000));

        vm.prank(DEVICE);
        hrc.anchorLog(ROBOT, 1, keccak256("r1"), from1, to1);

        vm.expectRevert(abi.encodeWithSelector(HRCSafetyLog.SeqOverlap.selector, ROBOT, to1, from2));
        vm.prank(DEVICE);
        hrc.anchorLog(ROBOT, 2, keccak256("r2"), from2, to2);
    }

    /// Chuỗi batch nối tiếp đúng luật (có thể có khoảng trống seq) => LUÔN thành công
    function testFuzz_SequentialBatchesSucceed(uint8 n, uint16 gap, uint16 len) public {
        n = uint8(bound(n, 1, 20));
        uint64 next = 1;
        for (uint64 i = 1; i <= n; i++) {
            uint64 from = next + (gap % 1000);
            uint64 to = from + (len % 1000);
            vm.prank(DEVICE);
            hrc.anchorLog(ROBOT, i, keccak256(abi.encode(i)), from, to);
            next = to + 1;
        }
        HRCSafetyLog.Robot memory r = hrc.getRobot(ROBOT);
        assertEq(r.lastBatchId, n);
        assertEq(r.lastToSeq, next - 1);
    }

    /// batchId khác 1 cho batch đầu tiên => LUÔN bị từ chối (chống replay/nhảy cóc)
    function testFuzz_WrongFirstBatchIdReverts(uint64 batchId) public {
        vm.assume(batchId != 1);
        vm.expectRevert(abi.encodeWithSelector(HRCSafetyLog.InvalidBatchId.selector, ROBOT, uint64(1), batchId));
        vm.prank(DEVICE);
        hrc.anchorLog(ROBOT, batchId, keccak256("r"), 1, 10);
    }

    /// Bất kỳ địa chỉ nào không phải DEVICE => KHÔNG BAO GIỜ ghi được hrc
    function testFuzz_NonDeviceCannotAnchor(address caller) public {
        vm.assume(caller != DEVICE);
        bytes32 role = hrc.DEVICE_ROLE();
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, caller, role)
        );
        vm.prank(caller);
        hrc.anchorLog(ROBOT, 1, keccak256("r"), 1, 10);
    }

    /// Cây 2 lá sorted-pair tự dựng => verifyRecord đúng cho cả 2 lá, sai khi sửa dữ liệu
    function testFuzz_TwoLeafTreeVerifies(uint64 seq, uint32 distance, uint8 zone) public {
        seq = uint64(bound(seq, 0, type(uint64).max - 1));
        HRCSafetyLog.LogRecord memory a = HRCSafetyLog.LogRecord(ROBOT, seq, 1_790_000_000_000, distance, 150, zone % 3, 0);
        HRCSafetyLog.LogRecord memory b =
            HRCSafetyLog.LogRecord(ROBOT, uint256(seq) + 1, 1_790_000_000_100, distance, 150, zone % 3, 1);
        bytes32 la = hrc.leafHash(a);
        bytes32 lb = hrc.leafHash(b);
        bytes32 root = la < lb ? keccak256(abi.encodePacked(la, lb)) : keccak256(abi.encodePacked(lb, la));

        vm.prank(DEVICE);
        hrc.anchorLog(ROBOT, 1, root, seq, seq + 1);

        bytes32[] memory proofA = new bytes32[](1);
        proofA[0] = lb;
        bytes32[] memory proofB = new bytes32[](1);
        proofB[0] = la;
        assertTrue(hrc.verifyRecord(1, a, proofA));
        assertTrue(hrc.verifyRecord(1, b, proofB));

        a.distanceMm += 1; // sửa 1 trường
        assertFalse(hrc.verifyRecord(1, a, proofA));
    }
}
