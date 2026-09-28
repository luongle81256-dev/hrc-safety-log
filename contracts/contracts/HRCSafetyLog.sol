// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {MerkleProof} from "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";

/**
 * @title HRCSafetyLog – Hộp đen an toàn và định danh cho robot hợp tác
 * @author Nhóm 01 – Cơ sở Blockchain và Ứng dụng
 * @notice Ghi bất biến Merkle root của log vận hành, ghi sự cố và khóa robot (LOCKED),
 *         khóa quyền operator vi phạm nhiều lần. Chỉ SUPERVISOR được mở khóa.
 * @dev Contract này KHÔNG nằm trong vòng điều khiển an toàn. E-Stop thật do ESP32 tự ngắt
 *      relay tại chỗ (fast lane). Contract là "hộp đen + trọng tài" (trust lane).
 *      Không có vòng lặp nào trong contract => chi phí gas mọi hàm là hằng số (trừ độ dài proof).
 *
 *      Leaf Merkle (interface đã chốt):
 *        keccak256(abi.encode(robot_id, seq, ts_ms, distance_mm, v_robot_mmps, zone, estop))
 *      Cây sorted-pair, tương thích OpenZeppelin MerkleProof.
 */
contract HRCSafetyLog is AccessControl {
    // =========================================================================
    // Hằng số & kiểu dữ liệu
    // =========================================================================

    /// @notice Ví backend đại diện cho robot, được ghi log và báo sự cố
    bytes32 public constant DEVICE_ROLE = keccak256("DEVICE_ROLE");
    /// @notice Người vận hành robot (check-in/check-out)
    bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");
    /// @notice Người giám sát, là người DUY NHẤT được mở khóa robot
    bytes32 public constant SUPERVISOR_ROLE = keccak256("SUPERVISOR_ROLE");
    /// @notice Kiểm toán viên, ghi kết quả đối soát log lên chuỗi
    bytes32 public constant AUDITOR_ROLE = keccak256("AUDITOR_ROLE");

    /// @notice Mức nghiêm trọng hợp lệ: 1 = cảnh báo, 2 = nguy hiểm, 3 = nghiêm trọng
    uint8 public constant MIN_SEVERITY = 1;
    uint8 public constant MAX_SEVERITY = 3;

    /// @notice Trạng thái robot. Robot mới đăng ký ở trạng thái ACTIVE.
    enum RobotState {
        ACTIVE,
        LOCKED
    }

    /// @dev Được xếp để gói vào 2 slot storage
    struct Robot {
        address device; // ví DEVICE được phép ghi thay robot này (định danh)
        RobotState state;
        bool registered;
        uint64 lastBatchId; // 0 = chưa có batch nào
        uint64 lastToSeq; // toSeq của batch gần nhất
        address currentOperator; // operator đang check-in (0 = không có)
    }

    /// @dev 2 slot storage
    struct Batch {
        bytes32 merkleRoot;
        uint64 fromSeq;
        uint64 toSeq;
        uint64 anchoredAt;
    }

    /// @dev 3 slot storage
    struct Incident {
        bytes32 evidenceHash;
        uint256 robotId;
        address operator;
        uint8 severity;
        uint64 timestamp;
    }

    /// @notice 1 dòng telemetry, đúng thứ tự trường của leaf Merkle
    struct LogRecord {
        uint256 robotId;
        uint256 seq;
        uint256 tsMs;
        uint256 distanceMm;
        uint256 vRobotMmps;
        uint256 zone;
        uint256 estop;
    }

    // =========================================================================
    // Storage
    // =========================================================================

    /// @notice Số lần vi phạm tối đa. Đạt ngưỡng này thì operator bị thu hồi OPERATOR_ROLE.
    uint256 public immutable MAX_VIOLATIONS;

    /// @notice Tổng số sự cố đã ghi (cũng là id của sự cố gần nhất, bắt đầu từ 1)
    uint256 public incidentCount;

    /// @notice Số lần vi phạm của từng operator
    mapping(address operator => uint256 count) public violationCount;

    mapping(uint256 robotId => Robot) private _robots;
    mapping(uint256 robotId => mapping(uint64 batchId => Batch)) private _batches;
    mapping(uint256 incidentId => Incident) private _incidents;

    // =========================================================================
    // Sự kiện
    // =========================================================================

    /// @notice Robot mới được đăng ký và gắn với ví DEVICE
    event RobotRegistered(uint256 indexed robotId, address indexed device);
    /// @notice Đổi ví DEVICE của robot (xoay khóa khi ví cũ bị lộ)
    event RobotDeviceChanged(uint256 indexed robotId, address indexed oldDevice, address indexed newDevice);
    /// @notice 1 batch log đã được neo lên chuỗi
    event LogAnchored(
        uint256 indexed robotId,
        uint64 indexed batchId,
        bytes32 merkleRoot,
        uint64 fromSeq,
        uint64 toSeq,
        uint256 timestamp
    );
    /// @notice Có sự cố, robot chuyển sang LOCKED
    event EmergencyStop(
        uint256 indexed robotId,
        uint256 indexed incidentId,
        address indexed operator,
        bytes32 evidenceHash,
        uint8 severity,
        uint256 timestamp
    );
    /// @notice SUPERVISOR đã mở khóa robot
    event Unlocked(uint256 indexed robotId, address indexed supervisor, uint256 timestamp);
    /// @notice Operator bị ghi thêm 1 lần vi phạm
    event ViolationRecorded(address indexed operator, uint256 violations);
    /// @notice Operator đạt MAX_VIOLATIONS và bị thu hồi OPERATOR_ROLE
    event OperatorBlocked(address indexed operator, uint256 violations);
    /// @notice Admin xóa bộ đếm vi phạm (sau khi operator được đào tạo lại)
    event ViolationsReset(address indexed operator, address indexed admin);
    /// @notice Operator nhận vận hành robot
    event OperatorCheckedIn(uint256 indexed robotId, address indexed operator);
    /// @notice Operator rời robot (tự rời, hoặc bị xóa khi unlock)
    event OperatorCheckedOut(uint256 indexed robotId, address indexed operator);
    /// @notice Auditor ghi kết quả đối soát log off-chain với Merkle root
    event AuditRecorded(
        uint256 indexed robotId,
        uint64 indexed batchId,
        address indexed auditor,
        bool passed,
        bytes32 reportHash,
        uint256 timestamp
    );

    // =========================================================================
    // Lỗi
    // =========================================================================

    error ZeroAddress();
    error InvalidMaxViolations();
    error RobotAlreadyRegistered(uint256 robotId);
    error RobotNotRegistered(uint256 robotId);
    error NotRobotDevice(uint256 robotId, address caller);
    error ZeroMerkleRoot();
    error InvalidSeqRange(uint64 fromSeq, uint64 toSeq);
    error InvalidBatchId(uint256 robotId, uint64 expected, uint64 provided);
    error SeqOverlap(uint256 robotId, uint64 lastToSeq, uint64 fromSeq);
    error ZeroEvidenceHash();
    error InvalidSeverity(uint8 severity);
    error RobotNotLocked(uint256 robotId);
    error RobotIsLocked(uint256 robotId);
    error RobotBusy(uint256 robotId, address currentOperator);
    error NotCurrentOperator(uint256 robotId, address caller);
    error OperatorIsBlocked(address operator, uint256 violations);
    error BatchNotFound(uint256 robotId, uint64 batchId);

    // =========================================================================
    // Khởi tạo & quản trị
    // =========================================================================

    /**
     * @param admin Ví nhận DEFAULT_ADMIN_ROLE (cấp/thu hồi mọi role, đăng ký robot)
     * @param maxViolations Ngưỡng vi phạm để khóa operator (> 0)
     */
    constructor(address admin, uint256 maxViolations) {
        if (admin == address(0)) revert ZeroAddress();
        if (maxViolations == 0) revert InvalidMaxViolations();
        MAX_VIOLATIONS = maxViolations;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    /**
     * @notice Đăng ký robot và gắn nó với 1 ví DEVICE (định danh robot trên chuỗi)
     * @dev Chỉ DEFAULT_ADMIN. Ví `device` vẫn phải có DEVICE_ROLE mới ghi được log.
     * @param robotId Mã robot (trùng `robot_id` trong telemetry)
     * @param device Ví backend được phép ghi log/sự cố thay cho robot này
     */
    function registerRobot(uint256 robotId, address device) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (device == address(0)) revert ZeroAddress();
        Robot storage r = _robots[robotId];
        if (r.registered) revert RobotAlreadyRegistered(robotId);
        r.registered = true;
        r.device = device;
        emit RobotRegistered(robotId, device);
    }

    /**
     * @notice Đổi ví DEVICE của robot (xoay khóa). Lịch sử batch được giữ nguyên.
     * @dev Chỉ DEFAULT_ADMIN
     * @param robotId Mã robot đã đăng ký
     * @param newDevice Ví DEVICE mới
     */
    function setRobotDevice(uint256 robotId, address newDevice) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newDevice == address(0)) revert ZeroAddress();
        Robot storage r = _requireRegistered(robotId);
        address old = r.device;
        r.device = newDevice;
        emit RobotDeviceChanged(robotId, old, newDevice);
    }

    /**
     * @notice Xóa bộ đếm vi phạm của operator để có thể cấp lại OPERATOR_ROLE
     * @dev Chỉ DEFAULT_ADMIN. Nếu chưa reset thì grantRole(OPERATOR_ROLE) sẽ revert.
     * @param operator Địa chỉ operator
     */
    function resetViolations(address operator) external onlyRole(DEFAULT_ADMIN_ROLE) {
        violationCount[operator] = 0;
        emit ViolationsReset(operator, msg.sender);
    }

    // =========================================================================
    // DEVICE: ghi log & sự cố
    // =========================================================================

    /**
     * @notice Neo Merkle root của 1 batch log off-chain lên chuỗi
     * @dev Chỉ ví DEVICE được gắn với robot. Chống replay/chồng lấn:
     *      - batchId phải đúng bằng lastBatchId + 1 (batch đầu tiên là 1)
     *      - fromSeq phải > toSeq của batch trước (không chồng lấn dải seq)
     *      Vẫn cho ghi khi robot LOCKED: hộp đen không được ngừng ghi.
     * @param robotId Mã robot
     * @param batchId Số thứ tự batch của robot này
     * @param merkleRoot Gốc cây Merkle của các leaf trong batch (khác 0)
     * @param fromSeq seq nhỏ nhất trong batch
     * @param toSeq seq lớn nhất trong batch (>= fromSeq)
     */
    function anchorLog(uint256 robotId, uint64 batchId, bytes32 merkleRoot, uint64 fromSeq, uint64 toSeq)
        external
    {
        Robot storage r = _requireRobotDevice(robotId);
        if (merkleRoot == bytes32(0)) revert ZeroMerkleRoot();
        if (fromSeq > toSeq) revert InvalidSeqRange(fromSeq, toSeq);

        uint64 lastBatchId = r.lastBatchId;
        uint64 expected = lastBatchId + 1;
        if (batchId != expected) revert InvalidBatchId(robotId, expected, batchId);
        if (lastBatchId != 0 && fromSeq <= r.lastToSeq) revert SeqOverlap(robotId, r.lastToSeq, fromSeq);

        r.lastBatchId = batchId;
        r.lastToSeq = toSeq;
        _batches[robotId][batchId] = Batch(merkleRoot, fromSeq, toSeq, uint64(block.timestamp));

        emit LogAnchored(robotId, batchId, merkleRoot, fromSeq, toSeq, block.timestamp);
    }

    /**
     * @notice Ghi sự cố an toàn: chuyển robot sang LOCKED và tăng số vi phạm của operator
     * @dev Chỉ ví DEVICE được gắn với robot. Nếu operator đạt MAX_VIOLATIONS thì bị thu hồi
     *      OPERATOR_ROLE (emit OperatorBlocked 1 lần duy nhất).
     * @param robotId Mã robot
     * @param evidenceHash Hash bằng chứng (khác 0), ví dụ keccak256 của các bản ghi quanh sự cố
     * @param severity Mức nghiêm trọng trong [MIN_SEVERITY, MAX_SEVERITY]
     * @param operator Operator chịu trách nhiệm, truyền address(0) nếu không quy lỗi cho ai
     *        (ví dụ lỗi do chính robot vượt tốc)
     * @return incidentId Id của sự cố vừa ghi
     */
    function reportIncident(uint256 robotId, bytes32 evidenceHash, uint8 severity, address operator)
        external
        returns (uint256 incidentId)
    {
        Robot storage r = _requireRobotDevice(robotId);
        if (evidenceHash == bytes32(0)) revert ZeroEvidenceHash();
        if (severity < MIN_SEVERITY || severity > MAX_SEVERITY) revert InvalidSeverity(severity);

        incidentId = ++incidentCount;
        _incidents[incidentId] = Incident(evidenceHash, robotId, operator, severity, uint64(block.timestamp));
        r.state = RobotState.LOCKED;
        emit EmergencyStop(robotId, incidentId, operator, evidenceHash, severity, block.timestamp);

        if (operator != address(0)) {
            uint256 v = ++violationCount[operator];
            emit ViolationRecorded(operator, v);
            // _revokeRole trả về false nếu operator đã bị thu hồi từ trước => không emit lặp
            if (v >= MAX_VIOLATIONS && _revokeRole(OPERATOR_ROLE, operator)) {
                emit OperatorBlocked(operator, v);
            }
        }
    }

    // =========================================================================
    // SUPERVISOR / OPERATOR / AUDITOR
    // =========================================================================

    /**
     * @notice Mở khóa robot sau khi đã xử lý sự cố
     * @dev Chỉ SUPERVISOR, chỉ khi robot đang LOCKED. Xóa operator hiện tại, buộc check-in lại.
     * @param robotId Mã robot
     */
    function unlock(uint256 robotId) external onlyRole(SUPERVISOR_ROLE) {
        Robot storage r = _requireRegistered(robotId);
        if (r.state != RobotState.LOCKED) revert RobotNotLocked(robotId);
        r.state = RobotState.ACTIVE;

        address op = r.currentOperator;
        if (op != address(0)) {
            r.currentOperator = address(0);
            emit OperatorCheckedOut(robotId, op);
        }
        emit Unlocked(robotId, msg.sender, block.timestamp);
    }

    /**
     * @notice Operator nhận vận hành robot. Operator đã bị khóa không còn role nên không gọi được.
     * @dev Chỉ OPERATOR, robot phải ACTIVE và chưa có operator khác
     * @param robotId Mã robot
     */
    function checkIn(uint256 robotId) external onlyRole(OPERATOR_ROLE) {
        Robot storage r = _requireRegistered(robotId);
        if (r.state == RobotState.LOCKED) revert RobotIsLocked(robotId);
        address current = r.currentOperator;
        if (current != address(0) && current != msg.sender) revert RobotBusy(robotId, current);
        r.currentOperator = msg.sender;
        emit OperatorCheckedIn(robotId, msg.sender);
    }

    /**
     * @notice Operator rời robot. Không yêu cầu role, để operator vừa bị khóa vẫn rời được.
     * @param robotId Mã robot
     */
    function checkOut(uint256 robotId) external {
        Robot storage r = _requireRegistered(robotId);
        if (r.currentOperator != msg.sender) revert NotCurrentOperator(robotId, msg.sender);
        r.currentOperator = address(0);
        emit OperatorCheckedOut(robotId, msg.sender);
    }

    /**
     * @notice Ghi kết quả đối soát log off-chain với Merkle root đã neo
     * @dev Chỉ AUDITOR, batch phải tồn tại. Chỉ emit event, không ghi storage (rẻ).
     * @param robotId Mã robot
     * @param batchId Batch đã đối soát
     * @param passed true nếu toàn bộ log khớp root
     * @param reportHash Hash của báo cáo đối soát (lưu off-chain)
     */
    function recordAudit(uint256 robotId, uint64 batchId, bool passed, bytes32 reportHash)
        external
        onlyRole(AUDITOR_ROLE)
    {
        if (_batches[robotId][batchId].merkleRoot == bytes32(0)) revert BatchNotFound(robotId, batchId);
        emit AuditRecorded(robotId, batchId, msg.sender, passed, reportHash, block.timestamp);
    }

    // =========================================================================
    // Kiểm chứng (view)
    // =========================================================================

    /**
     * @notice Kiểm tra 1 leaf có thuộc batch đã neo hay không
     * @dev CẢNH BÁO: nhận leaf thô nên có rủi ro second-preimage (truyền 1 node trung gian
     *      kèm proof ngắn hơn vẫn trả về true). Chỉ dùng khi leaf do chính bạn tính từ bản ghi.
     *      Để kiểm chứng 1 dòng log, dùng verifyRecord().
     * @param robotId Mã robot
     * @param batchId Batch cần kiểm tra
     * @param leaf Hash leaf
     * @param proof Merkle proof (sorted-pair)
     * @return true nếu hợp lệ; false nếu sai hoặc batch không tồn tại
     */
    function verifyLeaf(uint256 robotId, uint64 batchId, bytes32 leaf, bytes32[] calldata proof)
        external
        view
        returns (bool)
    {
        bytes32 root = _batches[robotId][batchId].merkleRoot;
        if (root == bytes32(0)) return false;
        return MerkleProof.verifyCalldata(proof, root, leaf);
    }

    /**
     * @notice Kiểm tra 1 dòng log (dữ liệu gốc) có thuộc batch đã neo hay không
     * @dev Contract tự tính leaf từ 7 trường (preimage 224 byte), khác preimage 64 byte của
     *      node trung gian => không bị second-preimage. Kiểm tra thêm seq nằm trong dải của batch.
     * @param batchId Batch cần kiểm tra (robot lấy từ record.robotId)
     * @param record Dòng log gốc
     * @param proof Merkle proof (sorted-pair)
     * @return true nếu hợp lệ
     */
    function verifyRecord(uint64 batchId, LogRecord calldata record, bytes32[] calldata proof)
        external
        view
        returns (bool)
    {
        Batch storage b = _batches[record.robotId][batchId];
        bytes32 root = b.merkleRoot;
        if (root == bytes32(0)) return false;
        if (record.seq < b.fromSeq || record.seq > b.toSeq) return false;
        return MerkleProof.verifyCalldata(proof, root, leafHash(record));
    }

    /**
     * @notice Tính leaf theo đúng interface đã chốt (để backend/test đối chiếu)
     * @param record Dòng log
     * @return keccak256(abi.encode(robot_id, seq, ts_ms, distance_mm, v_robot_mmps, zone, estop))
     */
    function leafHash(LogRecord calldata record) public pure returns (bytes32) {
        return keccak256(
            abi.encode(
                record.robotId,
                record.seq,
                record.tsMs,
                record.distanceMm,
                record.vRobotMmps,
                record.zone,
                record.estop
            )
        );
    }

    /// @notice Thông tin robot
    function getRobot(uint256 robotId) external view returns (Robot memory) {
        return _robots[robotId];
    }

    /// @notice Trạng thái robot (ACTIVE/LOCKED)
    function robotState(uint256 robotId) external view returns (RobotState) {
        return _robots[robotId].state;
    }

    /// @notice Thông tin 1 batch đã neo (merkleRoot = 0 nếu không tồn tại)
    function getBatch(uint256 robotId, uint64 batchId) external view returns (Batch memory) {
        return _batches[robotId][batchId];
    }

    /// @notice Thông tin 1 sự cố (evidenceHash = 0 nếu không tồn tại)
    function getIncident(uint256 incidentId) external view returns (Incident memory) {
        return _incidents[incidentId];
    }

    // =========================================================================
    // Nội bộ
    // =========================================================================

    /// @dev Chặn cấp lại OPERATOR_ROLE cho người đang bị khóa (phải resetViolations trước)
    function _grantRole(bytes32 role, address account) internal override returns (bool) {
        if (role == OPERATOR_ROLE) {
            uint256 v = violationCount[account];
            if (v >= MAX_VIOLATIONS) revert OperatorIsBlocked(account, v);
        }
        return super._grantRole(role, account);
    }

    function _requireRegistered(uint256 robotId) private view returns (Robot storage r) {
        r = _robots[robotId];
        if (!r.registered) revert RobotNotRegistered(robotId);
    }

    /// @dev Người gọi phải có DEVICE_ROLE VÀ đúng là ví được gắn với robot
    function _requireRobotDevice(uint256 robotId) private view returns (Robot storage r) {
        _checkRole(DEVICE_ROLE);
        r = _requireRegistered(robotId);
        if (r.device != msg.sender) revert NotRobotDevice(robotId, msg.sender);
    }
}
