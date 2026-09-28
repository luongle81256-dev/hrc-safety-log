// HMAC-SHA256 bằng mbedtls có sẵn trong ESP-IDF/Arduino-ESP32 (chỉ biên dịch cho ESP32)
#include <mbedtls/md.h>

#include "hrc_crypto.h"

namespace hrc {

void hmac_sha256(const uint8_t* key, size_t key_len, const uint8_t* msg, size_t msg_len, uint8_t out[32]) {
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_hmac(info, key, key_len, msg, msg_len, out);
}

}  // namespace hrc
