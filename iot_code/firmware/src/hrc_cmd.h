// Kiểm lệnh LOCK/UNLOCK từ backend – PHẢI giống hrc_sim/device.py::SimDevice.handle_command
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace hrc {

enum CmdResult : uint8_t { CMD_OK = 0, CMD_BAD_FORMAT, CMD_BAD_HMAC, CMD_REPLAY, CMD_STALE };
const char* cmd_result_name(CmdResult r);

struct Command {
  bool lock;  // true = LOCK, false = UNLOCK
  uint64_t nonce;
};

// Thứ tự kiểm: định dạng -> HMAC ("robot_id|cmd|nonce") -> nonce > last_nonce -> |nonce - now| <= skew (nếu có NTP)
CmdResult verify_command(const uint8_t* payload, size_t len, const uint8_t key[32], uint32_t robot_id,
                         uint64_t last_nonce, bool ntp_synced, uint64_t now_ms, Command* out);

}  // namespace hrc
