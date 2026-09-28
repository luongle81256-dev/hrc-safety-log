"""Mô hình "thế giới" cho simulator: vị trí người, tốc độ robot mong muốn, lỗi cảm biến + NHÃN ĐÚNG.

Mỗi kịch bản sinh 1 WorldSample cho mỗi chu kỳ (mặc định 100 ms). Có seed => tái lập được,
dùng chung cho demo (Phase 2) và sinh dataset có nhãn cho AI (Phase 3).

Nhãn (ground truth, KHÔNG gửi qua MQTT):
    NORMAL        vận hành bình thường
    INTRUSION     người thực sự ở trong khoảng cách bảo vệ S (tính ở tốc độ danh định)
    OVERSPEED     bộ điều khiển robot ra lệnh vượt ngưỡng v_robot_limit_mmps
    SENSOR_FAULT  cảm biến đo sai: spike (xung giả gần), dropout (mất mục tiêu), drift (lệch dần)
"""
from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass

from hrc_common.config import SSMParams, ssm_params
from hrc_common.ssm import protective_distance_mm

LABEL_NORMAL = "NORMAL"
LABEL_INTRUSION = "INTRUSION"
LABEL_OVERSPEED = "OVERSPEED"
LABEL_SENSOR_FAULT = "SENSOR_FAULT"
LABELS = (LABEL_NORMAL, LABEL_INTRUSION, LABEL_OVERSPEED, LABEL_SENSOR_FAULT)

# Giá trị VL53L0X trả về khi không thấy vật (GIẢ ĐỊNH theo thư viện Pololu VL53L0X – kiểm tra ở Phase 5)
TOF_OUT_OF_RANGE_MM = 8190


@dataclass(frozen=True, slots=True)
class WorldSample:
    t_ms: int  # thời gian mô phỏng từ lúc bắt đầu kịch bản
    true_distance_mm: int  # khoảng cách thật tới người
    measured_distance_mm: int  # giá trị cảm biến báo về (có nhiễu/lỗi)
    v_cmd_mmps: int  # tốc độ bộ điều khiển robot muốn chạy
    fault: str | None  # 'spike' | 'dropout' | 'drift' | None
    label: str


class Scenario(ABC):
    """Kịch bản cơ sở: lớp con chỉ cần mô tả người + tốc độ lệnh + lỗi cảm biến."""

    name = "base"

    def __init__(self, seed: int = 0, params: SSMParams | None = None, period_ms: int = 100):
        self.rng = random.Random(seed)
        self.p = params or ssm_params()
        self.dt = period_ms / 1000.0
        self.s_nominal = protective_distance_mm(self.p.v_robot_nominal_mmps, self.p)
        self.t_ms = 0
        self.period_ms = period_ms

    # ---- lớp con cài đặt ----
    @abstractmethod
    def _human(self) -> float:
        """Khoảng cách thật tới người (mm) ở chu kỳ hiện tại."""

    def _v_cmd(self) -> int:
        """Tốc độ robot mong muốn (mm/s). Mặc định: danh định ± dao động nhỏ."""
        return self.p.v_robot_nominal_mmps + self.rng.randint(-15, 15)

    def _fault(self, true_mm: float) -> tuple[float | None, str | None]:
        """Trả về (giá trị đo bị lỗi, loại lỗi) hoặc (None, None) nếu cảm biến bình thường."""
        return None, None

    # ---- khung chung ----
    def _measure(self, true_mm: float) -> int:
        if true_mm > self.p.distance_valid_max_mm:
            return TOF_OUT_OF_RANGE_MM
        sigma = max(2.0, 0.01 * true_mm)  # nhiễu đo GIẢ ĐỊNH ~1% (kiểm tra datasheet VL53L0X)
        return max(0, round(true_mm + self.rng.gauss(0.0, sigma)))

    def step(self) -> WorldSample:
        true_mm = max(0.0, self._human())
        v_cmd = max(0, self._v_cmd())
        faulty, fault = self._fault(true_mm)
        measured = round(faulty) if faulty is not None else self._measure(true_mm)
        measured = max(0, min(measured, TOF_OUT_OF_RANGE_MM))

        if true_mm <= self.s_nominal:
            label = LABEL_INTRUSION
        elif v_cmd > self.p.v_robot_limit_mmps:
            label = LABEL_OVERSPEED
        elif fault is not None:
            label = LABEL_SENSOR_FAULT
        else:
            label = LABEL_NORMAL

        sample = WorldSample(self.t_ms, round(true_mm), measured, v_cmd, fault, label)
        self.t_ms += self.period_ms
        return sample


class _Wander:
    """Người đi lại ngẫu nhiên trong [lo, hi] với tốc độ nhỏ."""

    def __init__(self, rng: random.Random, lo: float, hi: float, vmax: float = 60.0):
        self.rng, self.lo, self.hi, self.vmax = rng, lo, hi, vmax
        self.x = rng.uniform(lo, hi)
        self.v = 0.0

    def step(self, dt: float) -> float:
        self.v = max(-self.vmax, min(self.vmax, self.v + self.rng.gauss(0.0, 20.0)))
        self.x += self.v * dt
        if not self.lo <= self.x <= self.hi:
            self.x = min(self.hi, max(self.lo, self.x))
            self.v = -self.v
        return self.x


class NormalScenario(Scenario):
    """Bình thường: người ở xa (600–1100 mm), thỉnh thoảng đi hẳn ra ngoài tầm đo rồi quay lại.

    Việc ra khỏi tầm đo là HỢP LỆ (cảm biến báo 8190 dần dần, không đột ngột) – để AI không học nhầm
    "8190 luôn là lỗi" và phân biệt được với dropout (mất mục tiêu đột ngột khi người đang ở gần).
    """

    name = "normal"
    P_LEAVE = 0.003  # xác suất mỗi chu kỳ bắt đầu đi ra xa

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.walk = _Wander(self.rng, 600, 1100)
        self.excursion: str | None = None  # None | "out" | "away" | "back"
        self.ex_x = 0.0
        self.ex_t = 0.0

    def _human(self) -> float:
        if self.excursion is None:
            x = self.walk.step(self.dt)
            if self.rng.random() < self.P_LEAVE:
                self.excursion, self.ex_x = "out", x
                self.ex_far = self.rng.uniform(1300, 1600)
            return x
        if self.excursion == "out":
            self.ex_x += 150.0 * self.dt
            if self.ex_x >= self.ex_far:
                self.excursion, self.ex_t = "away", self.rng.uniform(3.0, 6.0)
        elif self.excursion == "away":
            self.ex_t -= self.dt
            if self.ex_t <= 0:
                self.excursion = "back"
        elif self.excursion == "back":
            self.ex_x -= 150.0 * self.dt
            if self.ex_x <= self.walk.x:
                self.excursion = None
        return self.ex_x


class ApproachScenario(Scenario):
    """Người tiến lại gần: đứng xa -> đi tới vào sâu vùng nguy hiểm -> dừng -> lùi ra. Lặp lại."""

    name = "approach"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._new_cycle()

    def _new_cycle(self) -> None:
        self.phase = "idle"
        self.phase_t = 0.0
        self.home = self.rng.uniform(800, 1000)
        # vào hẳn vùng DANGER (kể cả S khi robot đã đứng yên). S nhỏ (profile demo_close, S ~50 mm):
        # đi sâu tới ~S/4 – mô phỏng vị trí THẬT; cảm biến thật đo kém dưới ~3 cm (xem hrc_config.toml)
        lo = 60.0 if self.s_nominal - 20 > 60 else max(10.0, self.s_nominal * 0.25)
        self.target = self.rng.uniform(lo, max(lo + 1, self.s_nominal - 20))
        # không nhanh hơn vH giả định; vH rất nhỏ (demo_close) thì vẫn đi 150 mm/s để kịch bản không kéo dài
        self.v_in = self.rng.uniform(150, max(151.0, self.p.v_human_mmps))
        self.x = self.home

    def _human(self) -> float:
        self.phase_t += self.dt
        if self.phase == "idle" and self.phase_t >= 3.0:
            self.phase, self.phase_t = "in", 0.0
        elif self.phase == "in":
            self.x -= self.v_in * self.dt
            if self.x <= self.target:
                self.x, self.phase, self.phase_t = self.target, "hold", 0.0
        elif self.phase == "hold" and self.phase_t >= 2.0:
            self.phase, self.phase_t = "out", 0.0
        elif self.phase == "out":
            self.x += 200.0 * self.dt
            if self.x >= self.home:
                self._new_cycle()
        return self.x


class OverspeedScenario(Scenario):
    """Robot vượt tốc: người ở xa; cứ 10 s bộ điều khiển lỗi ra lệnh 300–450 mm/s trong 3 s."""

    name = "overspeed"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.walk = _Wander(self.rng, 700, 1100)
        self.v_fault = self.rng.randint(300, 450)

    def _human(self) -> float:
        return self.walk.step(self.dt)

    def _v_cmd(self) -> int:
        cycle_t = (self.t_ms % 10_000) / 1000.0
        if cycle_t == 0:
            self.v_fault = self.rng.randint(300, 450)
        if 5.0 <= cycle_t < 8.0:
            return self.v_fault
        return super()._v_cmd()


class SensorNoiseScenario(Scenario):
    """Nhiễu cảm biến: người ở khoảng cách vừa; chèn spike, dropout và drift."""

    name = "sensor_noise"
    P_SPIKE = 0.02
    P_DROPOUT = 0.01
    P_DRIFT = 0.004

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.walk = _Wander(self.rng, 450, 900)
        self.spike_left = 0
        self.spike_value = 0.0
        self.dropout_left = 0
        self.drift_left = 0.0
        self.drift_total = 0.0
        self.drift_amp = 0.0

    def _human(self) -> float:
        return self.walk.step(self.dt)

    def _fault(self, true_mm: float) -> tuple[float | None, str | None]:
        # Chỉ bắt đầu lỗi mới khi không có lỗi nào đang diễn ra
        idle = self.spike_left == 0 and self.dropout_left == 0 and self.drift_left <= 0
        if idle:
            r = self.rng.random()
            if r < self.P_SPIKE:
                self.spike_left = self.rng.randint(1, 2)
                self.spike_value = self.rng.uniform(20, 120)  # xung giả rất gần => DANGER giả
            elif r < self.P_SPIKE + self.P_DROPOUT:
                self.dropout_left = self.rng.randint(3, 10)  # mất mục tiêu dù người vẫn ở đó
            elif r < self.P_SPIKE + self.P_DROPOUT + self.P_DRIFT:
                self.drift_total = self.drift_left = 8.0
                self.drift_amp = self.rng.choice([-1, 1]) * self.rng.uniform(100, 250)

        if self.spike_left > 0:
            self.spike_left -= 1
            return self.spike_value, "spike"
        if self.dropout_left > 0:
            self.dropout_left -= 1
            return float(TOF_OUT_OF_RANGE_MM), "dropout"
        if self.drift_left > 0:
            self.drift_left -= self.dt
            bias = self.drift_amp * math.sin(math.pi * (1 - self.drift_left / self.drift_total))
            measured = self._measure(true_mm) + bias
            # Chỉ coi là lỗi khi độ lệch vượt sai số cảm biến cho phép
            return (measured, "drift") if abs(bias) > self.p.z_sensor_mm else (measured, None)
        return None, None


class MixedScenario(Scenario):
    """Xoay vòng các kịch bản (dùng cho demo dài và sinh dataset)."""

    name = "mixed"
    SEGMENTS = (("normal", 20_000), ("approach", 16_000), ("overspeed", 10_000), ("sensor_noise", 20_000))

    def __init__(self, seed: int = 0, params: SSMParams | None = None, period_ms: int = 100):
        super().__init__(seed, params, period_ms)
        self.subs = {
            name: SCENARIOS[name](seed + i, params, period_ms) for i, (name, _) in enumerate(self.SEGMENTS)
        }
        self.cycle_ms = sum(d for _, d in self.SEGMENTS)

    def _current(self) -> Scenario:
        t = self.t_ms % self.cycle_ms
        for name, dur in self.SEGMENTS:
            if t < dur:
                return self.subs[name]
            t -= dur
        raise AssertionError("không tới được")

    def _human(self) -> float:  # không dùng – step() được ghi đè
        raise NotImplementedError

    def step(self) -> WorldSample:
        s = self._current().step()
        out = WorldSample(self.t_ms, s.true_distance_mm, s.measured_distance_mm, s.v_cmd_mmps, s.fault, s.label)
        self.t_ms += self.period_ms
        return out


SCENARIOS: dict[str, type[Scenario]] = {
    "normal": NormalScenario,
    "approach": ApproachScenario,
    "overspeed": OverspeedScenario,
    "sensor_noise": SensorNoiseScenario,
    "mixed": MixedScenario,
}


def make_scenario(name: str, seed: int = 0, params: SSMParams | None = None, period_ms: int = 100) -> Scenario:
    try:
        return SCENARIOS[name](seed, params, period_ms)
    except KeyError as e:
        raise ValueError(f"Kịch bản '{name}' không tồn tại. Chọn: {sorted(SCENARIOS)}") from e
