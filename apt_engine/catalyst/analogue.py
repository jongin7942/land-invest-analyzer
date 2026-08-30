"""개통 선행사례 — "GTX 생기면 몇 % 오른다"를 만들지 않는다 (요구사항 6).

절대 상승률은 시장 전체가 오른 것인지 그 역 때문에 오른 것인지 구분하지 못한다.
그래서 **역세권과 비역세권의 가격비율**이 개통 전후로 어떻게 변했는지만 본다.
시장 전체의 등락은 분자·분모에서 함께 상쇄된다.

    개통 전  역세권 median / 비역세권 median = 1.05
    개통 후  역세권 median / 비역세권 median = 1.14
    → 상대적으로 +9%p. 이게 그 역이 만든 몫에 가깝다.

이걸 GTX-B·C 같은 미개통 노선에 **참고 범위로만** 쓴다. 확정 예측이 아니다.
표본이 적으면(역세권 3단지 미만 등) 아예 만들지 않는다.
"""
from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass

from apt_engine import units
from apt_engine.catalyst import transit
from apt_engine.trace import Calc, Evidence

# 이만큼은 있어야 중앙값이 의미를 갖는다.
MIN_SAMPLES = 3
# 개통 전후로 얼마나 떨어진 시점을 비교할 것인가.
DEFAULT_OFFSET_MONTHS = 12

SELF_DERIVED = Evidence(
    source="개통 선행사례 (자체 산출)",
    note="이미 개통된 역의 역세권/비역세권 대표가격 비율 변화. "
         "절대 상승률이 아니라 상대 비율이며, 미개통 노선에는 참고 범위로만 쓴다.")


@dataclass(frozen=True)
class Analogue:
    station_id: int | None
    station_name: str
    project_name: str
    opened_ym: str
    area_band: str
    radius_m: int
    before_ym: str
    after_ym: str
    near_n: int
    far_n: int
    ratio_before: float
    ratio_after: float
    calc: Calc

    @property
    def delta(self) -> float:
        return self.ratio_after - self.ratio_before


def shift_ym(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def _median_price(conn: sqlite3.Connection, complex_ids: list[int],
                  area_band: str, ym: str) -> tuple[float | None, int]:
    if not complex_ids:
        return None, 0
    marks = ",".join("?" for _ in complex_ids)
    rows = conn.execute(
        f"SELECT representative_price FROM price_snapshot "
        f"WHERE complex_id IN ({marks}) AND area_band = ? AND as_of_ym = ?",
        [*complex_ids, area_band, ym]).fetchall()
    values = [r[0] for r in rows if r[0]]
    if not values:
        return None, 0
    return statistics.median(values), len(values)


def _split_by_distance(conn: sqlite3.Connection, station_id: int, lawd_cd: str | None,
                       radius_m: int) -> tuple[list[int], list[int]]:
    """역세권 / 비역세권 단지 목록.

    비역세권은 **같은 시군구 안에서** 반경 밖인 단지다. 다른 시군구를 대조군으로
    쓰면 지역 차이가 섞여 들어온다.
    """
    near = [r[0] for r in conn.execute(
        "SELECT complex_id FROM station_distance WHERE station_id = ? AND meters <= ?",
        (station_id, radius_m))]
    if not lawd_cd:
        return near, []
    far = [r[0] for r in conn.execute(
        "SELECT c.id FROM complex c WHERE c.lawd_cd = ? AND c.id NOT IN "
        "(SELECT complex_id FROM station_distance WHERE station_id = ? AND meters <= ?)",
        (lawd_cd, station_id, radius_m))]
    return near, far


def build(conn: sqlite3.Connection, station_row: sqlite3.Row, *, area_band: str,
          radius_m: int = transit.NEAR_RADIUS_M,
          offset_months: int = DEFAULT_OFFSET_MONTHS) -> Analogue | None:
    """개통한 역 하나의 선행사례. 표본이 모자라면 None — 만들지 않는다."""
    opened_ym = station_row["opened_ym"]
    if not opened_ym or station_row["status"] != "개통":
        return None

    before_ym = shift_ym(opened_ym, -offset_months)
    after_ym = shift_ym(opened_ym, offset_months)

    near, far = _split_by_distance(conn, station_row["id"], station_row["lawd_cd"],
                                   radius_m)
    if len(near) < MIN_SAMPLES or len(far) < MIN_SAMPLES:
        return None

    nb, nb_n = _median_price(conn, near, area_band, before_ym)
    fb, fb_n = _median_price(conn, far, area_band, before_ym)
    na, na_n = _median_price(conn, near, area_band, after_ym)
    fa, fa_n = _median_price(conn, far, area_band, after_ym)

    if not all([nb, fb, na, fa]) or min(nb_n, fb_n, na_n, fa_n) < MIN_SAMPLES:
        return None

    ratio_before = nb / fb
    ratio_after = na / fa
    delta = ratio_after - ratio_before

    calc = Calc(
        value=delta, unit="%p",
        formula="(개통 후 역세권/비역세권) − (개통 전 역세권/비역세권)",
        inputs={
            "역": f"{station_row['project_name']} {station_row['name']}",
            "개통": opened_ym,
            "비교시점": f"{before_ym} → {after_ym} (개통 ±{offset_months}개월)",
            "역세권 기준": f"직선 {radius_m}m 이내",
            "면적": area_band,
        },
        intermediates={
            "개통 전": {"역세권": units.fmt_eok(int(nb)), "비역세권": units.fmt_eok(int(fb)),
                      "비율": units.fmt_pct(ratio_before)},
            "개통 후": {"역세권": units.fmt_eok(int(na)), "비역세권": units.fmt_eok(int(fa)),
                      "비율": units.fmt_pct(ratio_after)},
            "상대 변화": units.fmt_pct(delta, sign=True),
            "표본": f"역세권 {nb_n}/{na_n}개 · 비역세권 {fb_n}/{fa_n}개",
            "주의": "시장 전체의 등락은 분자·분모에서 상쇄된다. 절대 상승률이 아니다. "
                   "미개통 노선에 적용할 때는 참고 범위로만 쓴다.",
        },
        evidence=(SELF_DERIVED,),
        grade="CONFIRMED",     # 실거래에서 나온 관측값이다
    )
    return Analogue(station_row["id"], station_row["name"], station_row["project_name"],
                    opened_ym, area_band, radius_m, before_ym, after_ym,
                    len(near), len(far), ratio_before, ratio_after, calc)


def summarize(analogues: list[Analogue]) -> Calc | None:
    """여러 선행사례의 범위. 하나로 단정하지 않고 최소~최대를 보여준다."""
    if not analogues:
        return None
    deltas = sorted(a.delta for a in analogues)
    return Calc(
        value=statistics.median(deltas), unit="%p",
        formula=f"개통 선행사례 {len(deltas)}건의 상대 변화 중앙값",
        inputs={"사례": [f"{a.project_name} {a.station_name}({a.opened_ym})"
                        for a in analogues]},
        intermediates={
            "범위": f"{units.fmt_pct(deltas[0], sign=True)} ~ "
                   f"{units.fmt_pct(deltas[-1], sign=True)}",
            "중앙값": units.fmt_pct(statistics.median(deltas), sign=True),
            "주의": ("사례가 적을수록 범위를 넓게 봐야 한다. "
                    "노선·지역·시기가 다르면 같은 결과가 나오지 않는다."),
        },
        evidence=(SELF_DERIVED,),
        # 미개통 노선에 적용하는 순간 추정이 된다.
        grade="ESTIMATED",
    )
