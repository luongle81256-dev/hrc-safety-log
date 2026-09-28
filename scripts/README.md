# /scripts

| Script | Phase | Việc |
|---|---|---|
| `gen_config.py` | 0 | Sinh `hrc_params.h` từ config. Chạy `--check` để phát hiện lệch |
| `mqtt_smoke_test.py` | 0 | Nghiệm thu broker: pub → sub vòng về, đo RTT |
| `demo_scenario.py` | 8 | Chạy kịch bản demo end-to-end |
| `tamper_demo.py` | 8 | Sửa 1 dòng log off-chain, verify báo sai |
| `verify_log.py` | 8 | Đối chiếu log off-chain với Merkle root on-chain |
