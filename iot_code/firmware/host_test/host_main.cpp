// Chương trình kiểm chứng logic firmware trên máy tính – điều khiển qua stdin, mỗi dòng 1 lệnh, in 1 dòng kết quả.
// Được tests/test_firmware_host.py dùng để so từng bước với simulator Python.
//
//   VEC                                   -> OK | FAIL   (test vector HMAC + tiện ích)
//   FLRESET <locked>                      -> OK          (FastLane mới = khởi động lại)
//   FL <now_ms> <dist> <v_cmd> <locked>   -> "<zone> <estop> <v_meas> <v_next>"
//   CMDRESET <last_nonce>                 -> OK
//   CMD <payload_hex> <now_ms> <ntp>      -> OK|BAD_FORMAT|BAD_HMAC|REPLAY|STALE (OK => cập nhật last_nonce)
//   TEL <robot> <seq> <ts> <d> <v> <zone> <estop>   -> JSON telemetry đã ký (khóa test 00..1f)
//   HB <robot> <ts> <state> <estop> <cfg>           -> JSON heartbeat đã ký
//   FEAT <20 d> <20 v> <20 zone> <20 estop>  -> 20 đặc trưng (double)
//   NN <20 int8>                          -> 20 int8     (runtime INT8)
//   EDGE <20 đặc trưng>                   -> "<mse> <anomaly>"
//   EDGEGOLD                              -> OK | FAIL   (vector tự kiểm tra trong header)
//   NVSRESET                              -> OK          (chip mới: NVS rỗng rồi khởi động)
//   BOOT                                  -> OK          (SeqAllocator mới trên cùng NVS = reset chip)
//   SEQ                                   -> số seq kế tiếp
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <map>
#include <memory>
#include <string>

#include "hrc_cmd.h"
#include "hrc_crypto.h"
#include "hrc_features.h"
#include "hrc_int8_nn.h"
#include "hrc_logic.h"

using namespace hrc;

class MemNvs : public NvsStore {
 public:
  uint64_t get_u64(const char* k, uint64_t d) override { return u64_.count(k) ? u64_[k] : d; }
  void put_u64(const char* k, uint64_t v) override { u64_[k] = v; }
  bool get_bool(const char* k, bool d) override { return b_.count(k) ? b_[k] : d; }
  void put_bool(const char* k, bool v) override { b_[k] = v; }

 private:
  std::map<std::string, uint64_t> u64_;
  std::map<std::string, bool> b_;
};

static bool run_vectors() {
  if (!crypto_self_test()) return false;
  uint8_t k[32];
  if (parse_key_hex("0000000000000000000000000000000000000000000000000000000000000000", k)) return false;
  if (parse_key_hex("abc", k)) return false;
  if (!parse_key_hex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", k) || k[31] != 0x1f) return false;
  const uint8_t a[3] = {1, 2, 3}, b[3] = {1, 2, 4};
  if (!ct_equal(a, a, 3) || ct_equal(a, b, 3)) return false;
  char buf[64];
  canon_telemetry(buf, sizeof(buf), 1, 0, 1790000000000ULL, 350, 150, 0, 0);
  return strcmp(buf, "1|0|1790000000000|350|150|0|0") == 0;
}

int main() {
  uint8_t key[32];
  for (int i = 0; i < 32; i++) key[i] = (uint8_t)i;
  MemNvs nvs;
  std::unique_ptr<FastLane> fl(new FastLane(false));
  std::unique_ptr<SeqAllocator> seq(new SeqAllocator(nvs));
  uint64_t last_nonce = 0;

  static char line[4096];
  while (fgets(line, sizeof(line), stdin)) {
    char op[16] = {0};
    if (sscanf(line, "%15s", op) != 1) continue;
    const char* rest = line + strlen(op);
    if (!strcmp(op, "VEC")) {
      puts(run_vectors() ? "OK" : "FAIL");
    } else if (!strcmp(op, "FLRESET")) {
      int locked = 0;
      sscanf(rest, "%d", &locked);
      fl.reset(new FastLane(locked != 0));
      puts("OK");
    } else if (!strcmp(op, "FL")) {
      unsigned long long now = 0;
      unsigned d = 0, v = 0;
      int locked = 0;
      sscanf(rest, "%llu %u %u %d", &now, &d, &v, &locked);
      fl->set_locked(locked != 0);
      const FastLaneOutput o = fl->step((uint32_t)now, d, v);  // millis() 32 bit: phép trừ vẫn đúng khi tràn
      printf("%u %d %u %u\n", (unsigned)o.zone, (int)o.estop, (unsigned)o.v_meas, (unsigned)o.v_next);
    } else if (!strcmp(op, "CMDRESET")) {
      sscanf(rest, "%" SCNu64, &last_nonce);
      puts("OK");
    } else if (!strcmp(op, "CMD")) {
      char hex[2050] = {0};
      unsigned long long now = 0;
      int ntp = 1;
      sscanf(rest, "%2049s %llu %d", hex, &now, &ntp);
      uint8_t payload[1024];
      size_t n = strlen(hex) / 2;
      for (size_t i = 0; i < n; i++) {
        unsigned b;
        sscanf(hex + 2 * i, "%2x", &b);
        payload[i] = (uint8_t)b;
      }
      Command c;
      const CmdResult r = verify_command(payload, n, key, 1, last_nonce, ntp != 0, now, &c);
      if (r == CMD_OK) last_nonce = c.nonce;
      puts(cmd_result_name(r));
    } else if (!strcmp(op, "TEL")) {
      unsigned robot, d, v, zone, estop;
      unsigned long long s, ts;
      sscanf(rest, "%u %llu %llu %u %u %u %u", &robot, &s, &ts, &d, &v, &zone, &estop);
      char canon[128], mac[65], json[256];
      canon_telemetry(canon, sizeof(canon), robot, s, ts, d, v, (uint8_t)zone, (uint8_t)estop);
      sign_hex(key, canon, mac);
      json_telemetry(json, sizeof(json), robot, s, ts, d, v, (uint8_t)zone, (uint8_t)estop, mac);
      puts(json);
    } else if (!strcmp(op, "HB")) {
      unsigned robot, state, estop;
      unsigned long long ts;
      char cfg[32] = {0};
      sscanf(rest, "%u %llu %u %u %31s", &robot, &ts, &state, &estop, cfg);
      char canon[96], mac[65], json[192];
      canon_heartbeat(canon, sizeof(canon), robot, ts, (uint8_t)state, (uint8_t)estop, cfg);
      sign_hex(key, canon, mac);
      json_heartbeat(json, sizeof(json), robot, ts, (uint8_t)state, (uint8_t)estop, cfg, mac);
      puts(json);
    } else if (!strcmp(op, "BOOT")) {
      seq.reset(new SeqAllocator(nvs));
      puts("OK");
    } else if (!strcmp(op, "FEAT")) {  // FEAT d0..d19 v0..v19 z0..z19 e0..e19 -> 20 đặc trưng (double)
      uint32_t d[HRC_EDGE_WINDOW], v[HRC_EDGE_WINDOW];
      uint8_t z[HRC_EDGE_WINDOW], e[HRC_EDGE_WINDOW];
      const char* p = rest;
      int n = 0;
      unsigned tmp;
      for (int i = 0; i < 4 * HRC_EDGE_WINDOW; i++) {
        sscanf(p, " %u%n", &tmp, &n);
        p += n;
        const int k = i % HRC_EDGE_WINDOW, which = i / HRC_EDGE_WINDOW;
        if (which == 0) d[k] = tmp; else if (which == 1) v[k] = tmp; else if (which == 2) z[k] = (uint8_t)tmp; else e[k] = (uint8_t)tmp;
      }
      double f[HRC_EDGE_N_FEATURES];
      compute_features(d, v, z, e, f);
      for (int i = 0; i < HRC_EDGE_N_FEATURES; i++) printf(i ? " %.17g" : "%.17g", f[i]);
      printf("\n");
    } else if (!strcmp(op, "NN")) {  // NN q0..q19 (int8) -> 20 int8
      int8_t in[HRC_EDGE_N_FEATURES], out[HRC_EDGE_N_FEATURES];
      const char* p = rest;
      int n = 0, tmp = 0;
      for (int i = 0; i < HRC_EDGE_N_FEATURES; i++) {
        sscanf(p, " %d%n", &tmp, &n);
        p += n;
        in[i] = (int8_t)tmp;
      }
      edge_nn_run(in, out);
      for (int i = 0; i < HRC_EDGE_N_FEATURES; i++) printf(i ? " %d" : "%d", out[i]);
      printf("\n");
    } else if (!strcmp(op, "EDGE")) {  // EDGE f0..f19 (đặc trưng thô) -> "<error> <anomaly>"
      double f[HRC_EDGE_N_FEATURES];
      const char* p = rest;
      int n = 0;
      for (int i = 0; i < HRC_EDGE_N_FEATURES; i++) {
        sscanf(p, " %lf%n", &f[i], &n);
        p += n;
      }
      const EdgeResult r = edge_infer(f);
      printf("%.9g %d\n", r.error, (int)r.anomaly);
    } else if (!strcmp(op, "EDGEGOLD")) {
      puts(edge_self_test() ? "OK" : "FAIL");
    } else if (!strcmp(op, "NVSRESET")) {  // chip mới tinh (NVS rỗng) rồi khởi động
      seq.reset();
      nvs = MemNvs();
      seq.reset(new SeqAllocator(nvs));
      puts("OK");
    } else if (!strcmp(op, "US")) {  // US <echo_started> <echo_ended> <echo_us> -> "<distance_mm> <sensor_ok>"
      int started = 0, ended = 0;
      unsigned echo_us = 0;
      sscanf(rest, "%d %d %u", &started, &ended, &echo_us);
      const RangeSample r = ultrasonic_range(started != 0, ended != 0, echo_us);
      printf("%u %d\n", (unsigned)r.distance_mm, (int)r.sensor_ok);
    } else if (!strcmp(op, "SEQ")) {
      printf("%" PRIu64 "\n", seq->next());
    } else {
      puts("UNKNOWN");
    }
    fflush(stdout);
  }
  return 0;
}
