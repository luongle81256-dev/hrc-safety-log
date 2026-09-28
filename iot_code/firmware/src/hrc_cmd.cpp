#include "hrc_cmd.h"

#include <ArduinoJson.h>
#include <string.h>

#include "hrc_crypto.h"
#include "hrc_logic.h"

namespace hrc {

const char* cmd_result_name(CmdResult r) {
  switch (r) {
    case CMD_OK: return "OK";
    case CMD_BAD_FORMAT: return "BAD_FORMAT";
    case CMD_BAD_HMAC: return "BAD_HMAC";
    case CMD_REPLAY: return "REPLAY";
    case CMD_STALE: return "STALE";
  }
  return "?";
}

// Đếm số lần chuỗi `needle` xuất hiện trong payload thô
static int count_occurrences(const uint8_t* p, size_t len, const char* needle) {
  const size_t m = strlen(needle);
  int n = 0;
  for (size_t i = 0; i + m <= len; i++) {
    if (memcmp(p + i, needle, m) == 0) n++;
  }
  return n;
}

CmdResult verify_command(const uint8_t* payload, size_t len, const uint8_t key[32], uint32_t robot_id,
                         uint64_t last_nonce, bool ntp_synced, uint64_t now_ms, Command* out) {
  if (len == 0 || len > 512) return CMD_BAD_FORMAT;
  // Chống "parser differential": ArduinoJson GỘP khóa trùng thay vì báo lỗi, còn backend (Python) từ chối.
  // (1) cấm escape '\' (không ai gửi hợp lệ; chặn cách viết khóa kiểu "cmd")
  // (2) mỗi khóa xuất hiện ĐÚNG 1 lần trong payload thô (giá trị hợp lệ không thể chứa chuỗi này)
  if (memchr(payload, '\\', len) != nullptr) return CMD_BAD_FORMAT;
  if (count_occurrences(payload, len, "\"cmd\"") != 1 || count_occurrences(payload, len, "\"nonce\"") != 1 ||
      count_occurrences(payload, len, "\"hmac\"") != 1) {
    return CMD_BAD_FORMAT;
  }
  JsonDocument doc;
  if (deserializeJson(doc, payload, len) != DeserializationError::Ok) return CMD_BAD_FORMAT;
  JsonObject obj = doc.as<JsonObject>();
  // Đúng 3 khóa cmd/nonce/hmac (giống parse_command của Python)
  if (obj.isNull() || obj.size() != 3 || !obj["cmd"].is<const char*>() || !obj["nonce"].is<uint64_t>() ||
      !obj["hmac"].is<const char*>()) {
    return CMD_BAD_FORMAT;
  }
  const char* cmd = obj["cmd"].as<const char*>();
  const uint64_t nonce = obj["nonce"].as<uint64_t>();
  const char* mac_hex = obj["hmac"].as<const char*>();
  const bool is_lock = strcmp(cmd, "LOCK") == 0;
  if ((!is_lock && strcmp(cmd, "UNLOCK") != 0) || nonce > INT63_MAX || !is_lower_hex(mac_hex, 64)) {
    return CMD_BAD_FORMAT;
  }

  char canonical[64];
  canon_command(canonical, sizeof(canonical), robot_id, cmd, nonce);
  uint8_t expected[32], got[32];
  hmac_sha256(key, 32, reinterpret_cast<const uint8_t*>(canonical), strlen(canonical), expected);
  for (int i = 0; i < 32; i++) {
    const char h = mac_hex[2 * i], l = mac_hex[2 * i + 1];
    got[i] = (uint8_t)(((h <= '9' ? h - '0' : h - 'a' + 10) << 4) | (l <= '9' ? l - '0' : l - 'a' + 10));
  }
  if (!ct_equal(expected, got, 32)) return CMD_BAD_HMAC;
  if (nonce <= last_nonce) return CMD_REPLAY;
  if (ntp_synced) {
    const uint64_t diff = nonce > now_ms ? nonce - now_ms : now_ms - nonce;
    if (diff > HRC_CMD_MAX_SKEW_MS) return CMD_STALE;
  }
  out->lock = is_lock;
  out->nonce = nonce;
  return CMD_OK;
}

}  // namespace hrc
