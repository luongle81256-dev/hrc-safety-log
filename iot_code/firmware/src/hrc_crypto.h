// HMAC-SHA256 trừu tượng: ESP32 dùng mbedtls (hrc_crypto_mbedtls.cpp);
// test trên máy tính dùng bản cài đặt tham chiếu riêng (test/host/sha256_ref.cpp).
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace hrc {

void hmac_sha256(const uint8_t* key, size_t key_len, const uint8_t* msg, size_t msg_len, uint8_t out[32]);

// Ký chuỗi canonical -> hex chữ thường 64 ký tự (out cần 65 byte)
void sign_hex(const uint8_t key[32], const char* canonical, char out_hex[65]);

// Tự kiểm tra lúc khởi động: HMAC của test vector (docs/INTERFACES.md) phải đúng từng byte
bool crypto_self_test();

}  // namespace hrc
