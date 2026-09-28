"""SSM: công thức, biên phân vùng, và bản C (header tự sinh) khớp bản Python."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from hrc_common.config import ROOT_DIR, ZONE_DANGER, ZONE_SAFE, ZONE_WARNING, ssm_params
from hrc_common.ssm import classify_zone, protective_distance_mm


def test_formula_demo_profile():
    p = ssm_params("demo_tabletop")
    # 300*(100+150)/1000 = 75 ; 150*100/1000 = 15 ; +15+20+5+30 = 160
    assert protective_distance_mm(150, p) == 160
    assert protective_distance_mm(0, p) == 145


def test_rounds_up():
    p = ssm_params("demo_tabletop")
    # vR = 1 mm/s => 0.1 mm động thêm => làm tròn LÊN thành 1 mm
    assert protective_distance_mm(1, p) == protective_distance_mm(0, p) + 1


def test_zone_boundaries():
    p = ssm_params()
    s = protective_distance_mm(150, p)
    assert classify_zone(s, 150, p) == ZONE_DANGER  # biên thuộc vùng nguy hiểm hơn
    assert classify_zone(s + 1, 150, p) == ZONE_WARNING
    assert classify_zone(s + p.warning_margin_mm, 150, p) == ZONE_WARNING
    assert classify_zone(s + p.warning_margin_mm + 1, 150, p) == ZONE_SAFE
    assert classify_zone(0, 0, p) == ZONE_DANGER  # cảm biến bị che sát => DANGER


def test_faster_robot_needs_more_distance():
    p = ssm_params()
    assert protective_distance_mm(300, p) > protective_distance_mm(150, p)


def test_negative_speed_rejected():
    with pytest.raises(ValueError):
        protective_distance_mm(-1)


C_PROGRAM = r"""
#include <stdio.h>
#include "hrc_params.h"
int main(void) {
    for (unsigned v = 0; v <= 1000; v += 7)
        for (unsigned d = 0; d <= 1500; d += 3)
            printf("%u %u %u %u\n", d, v, (unsigned)hrc_ssm_protective_distance_mm(v),
                   (unsigned)hrc_ssm_classify_zone(d, v));
    return 0;
}
"""


@pytest.mark.gcc
@pytest.mark.skipif(shutil.which("gcc") is None, reason="không có gcc")
def test_c_header_matches_python(tmp_path):
    src, exe = tmp_path / "ssm.c", tmp_path / "ssm.exe"
    src.write_text(C_PROGRAM, encoding="utf-8")
    inc = ROOT_DIR / "iot_code" / "firmware" / "include"
    subprocess.run(["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", f"-I{inc}", str(src), "-o", str(exe)],
                   check=True, capture_output=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout.split("\n")
    rows = [tuple(map(int, line.split())) for line in out if line.strip()]
    assert len(rows) > 70_000
    bad = [r for r in rows if (protective_distance_mm(r[1]), classify_zone(r[0], r[1])) != (r[2], r[3])]
    assert bad == []
