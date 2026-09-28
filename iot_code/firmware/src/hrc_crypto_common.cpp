// Phần dùng chung (không phụ thuộc thư viện mật mã cụ thể)
#include <string.h>

#include "hrc_crypto.h"
#include "hrc_logic.h"

namespace hrc {

void sign_hex(const uint8_t key[32], const char* canonical, char out_hex[65]) {
  uint8_t mac[32];
  hmac_sha256(key, 32, reinterpret_cast<const uint8_t*>(canonical), strlen(canonical), mac);
  to_hex(mac, 32, out_hex);
}

bool crypto_self_test() {
  // Test vector trong docs/INTERFACES.md (khóa = 00 01 ... 1f), đã đối chiếu Python + .NET
  static const char* const kMsgs[] = {
      "1|42|1790000000000|350|150|1|0",
      "1|LOCK|1790000000123",
      "hb|1|1790000000500|0|0|99c3bd15cba71f37",
  };
  static const char* const kMacs[] = {
      "0544056a530339a4f2a92ea818bb779c49c2e506d580c1f68f29cc13ad985d07",
      "430facbad90a2666d5846882c8bca4e719dcf26c49a0966fd458f7a84a78fad4",
      "2f836d5d8aafc121930eff7f34322a583b56ca03e3454303c47a41491a34d8f8",
  };
  uint8_t key[32];
  for (int i = 0; i < 32; i++) key[i] = (uint8_t)i;
  char hex[65];
  for (int i = 0; i < 3; i++) {
    sign_hex(key, kMsgs[i], hex);
    if (strcmp(hex, kMacs[i]) != 0) return false;
  }
  return true;
}

}  // namespace hrc
