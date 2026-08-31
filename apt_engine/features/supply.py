"""공급 — 절대물량이 아니라 stock 대비 비율 (지시서 §13).

> Supply Ratio = 실제 입주예정 물량 / 기존 주택 stock

같은 3,000세대라도 stock 이 5만 세대인 지역과 8천 세대인 지역은 충격이 다르다.
절대물량으로 비교하면 큰 도시가 늘 "공급 과다" 로 나온다.

그리고 단계마다 신뢰도가 다르다.

    계획 → 분양 → 착공 → 입주예정 → 입주완료

**실제 입주예정에 가장 큰 weight** 를 준다(§13). 계획 단계는 무산되거나 몇 년씩
밀린다. 그래서 물량을 그냥 더하지 않고 단계별 가중치를 곱한 '실효 물량' 을 쓴다.

Supply Cliff: 공급이 많았던 지역에서 공급이 **끝나는** 시점. 물량이 절벽처럼
줄면 그 뒤 몇 년은 공급 공백이 된다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from apt_engine import geo
from apt_engine.blind import cutoff as cutoff_mod
from apt_engine.features.base import Feature, Status, combine
from apt_engine.trace import Calc

HORIZONS = (1, 2, 3, 5)

# 단계별 실현 가중치. **관측된 실현율이 아니라 판정 기준**이다.
# 백테스트가 "계획 단계 물량이 실제로 몇 % 실현됐나" 로 대체한다.
STAGE_WEIGHT = {"계획": 0.25, "분양": 0.60, "착공": 0.85,
                "입주예정": 1.00, "입주완료": 1.00}

STAGE_NOTE = ("단계 가중치는 관측된 실현율이 아니라 판정 기준입니다. "
              "백테스트(§55)가 실제 실현율로 대체합니다")

# 경쟁 관계 분류 (§13). 같은 급의 공급이 가장 아프다.
SAME_TIER = "동급경쟁"
SUPERIOR = "상급상품"
ADJACENT = "인접대체"
LIFE_ZONE_DONE = "생활권완성"

NEAR_RADIUS_M = 3000       # 이 안쪽을 직접 경쟁으로 본다
FAR_RADIUS_M = 5000

# 공급이 절벽처럼 줄었다고 볼 기준. 역시 판정 기준이다.
CLIFF_DROP = 0.5


@dataclass(frozen=True)
class SupplyItem:
    complex_name: str
    households: int
    move_in_ym: str
    stage: str
    kind: str | None
    meters: float | None
    category: str

    @property
    def effective(self) -> float:
        return self.households * STAGE_WEIGHT.get(self.stage, 0.25)


@dataclass(frozen=True)
class SupplyView:
    complex_id: int
    stock: int | None
    basis: str
    end_ym: str = ""
    items: list[SupplyItem] = field(default_factory=list)
    by_horizon: dict[int, float] = field(default_factory=dict)
    raw_by_horizon: dict[int, int] = field(default_factory=dict)

    def ratio(self, years: int) -> float | None:
        if not self.stock:
            return None
        return self.by_horizon.get(years, 0.0) / self.stock


def _stock_near(conn: sqlite3.Connection, lat: float | None, lon: float | None,
                lawd_cd: str, radius_m: int) -> tuple[int | None, str]:
    """기존 주택 stock. 좌표가 있으면 반경, 없으면 시군구 전체.

    **어느 기준으로 셌는지 함께 돌려준다** — 반경과 시군구를 섞어 비교하면
    비율이 두 배씩 어긋난다.
    """
    if lat is None or lon is None:
        row = conn.execute(
            "SELECT SUM(apt_households) FROM complex WHERE lawd_cd = ? "
            " AND apt_households IS NOT NULL", (lawd_cd,)).fetchone()
        total = row[0] if row and row[0] else None
        return (int(total) if total else None), f"시군구 {lawd_cd} 전체 (좌표 없음)"

    rows = conn.execute(
        "SELECT lat, lon, apt_households FROM complex "
        " WHERE lat IS NOT NULL AND lon IS NOT NULL AND apt_households IS NOT NULL "
        "   AND lawd_cd = ?", (lawd_cd,)).fetchall()
    total = sum(int(r["apt_households"]) for r in rows
                if geo.haversine_m(lat, lon, r["lat"], r["lon"]) <= radius_m)
    return (total or None), f"반경 {radius_m/1000:g}km 이내"


def _categorize(item_kind: str | None, meters: float | None) -> str:
    """공급을 경쟁 관계로 나눈다. 모르면 가장 보수적인 '동급경쟁' 으로 둔다."""
    if item_kind in ("재건축", "재개발"):
        # 정비사업 신축은 대개 상급 상품으로 나온다
        return SUPERIOR
    if meters is None:
        return SAME_TIER
    if meters <= NEAR_RADIUS_M:
        return SAME_TIER
    if meters <= FAR_RADIUS_M:
        return ADJACENT
    return LIFE_ZONE_DONE


def build(conn: sqlite3.Connection, complex_id: int, *,
          as_of: cutoff_mod.AsOf, radius_m: int = NEAR_RADIUS_M) -> SupplyView:
    """이 단지 주변의 향후 공급."""
    observable = as_of.observable
    row = conn.execute(
        "SELECT lat, lon, lawd_cd FROM complex WHERE id = ?", (complex_id,)).fetchone()
    if row is None:
        return SupplyView(complex_id, None, "단지를 찾을 수 없음", observable.ym)

    stock, basis = _stock_near(conn, row["lat"], row["lon"], row["lawd_cd"], radius_m)

    end_ym = observable.ym
    horizon_ym = {y: _shift(end_ym, y * 12) for y in HORIZONS}
    with cutoff_mod.guard(conn, observable) as g:
        # **컷오프 이전에 알려진 계획**만 본다. 나중에 발표된 공급을 과거 모델이
        # 알고 있으면 look-ahead 다(§18).
        rows = g.execute(
            "SELECT complex_name, households, move_in_ym, stage, kind, lat, lon "
            "  FROM supply_plan "
            " WHERE lawd_cd = ? AND move_in_ym > ? AND move_in_ym <= ? "
            # 그 시점에 **발표돼 있던** 계획만 본다. 발표시점이 비면 언제 알았는지
            # 모르는 것이므로 백테스트에서 쓰지 않는다(§18).
            "   AND announced_ym IS NOT NULL AND announced_ym <= ?",
            (row["lawd_cd"], end_ym, horizon_ym[max(HORIZONS)], observable.ym)
        ).fetchall()

    items: list[SupplyItem] = []
    for r in rows:
        meters = None
        if row["lat"] is not None and r["lat"] is not None:
            meters = geo.haversine_m(row["lat"], row["lon"], r["lat"], r["lon"])
            if meters > FAR_RADIUS_M:
                continue
        items.append(SupplyItem(
            r["complex_name"], int(r["households"]), r["move_in_ym"], r["stage"],
            r["kind"], meters, _categorize(r["kind"], meters)))

    by_h: dict[int, float] = {}
    raw_h: dict[int, int] = {}
    for y in HORIZONS:
        window = [i for i in items if i.move_in_ym <= horizon_ym[y]]
        by_h[y] = sum(i.effective for i in window)
        raw_h[y] = sum(i.households for i in window)

    return SupplyView(complex_id, stock, basis, end_ym, items, by_h, raw_h)


def ratio_feature(view: SupplyView, years: int = 3) -> Feature:
    """Supply Ratio — 실효 입주물량 ÷ 기존 stock."""
    key = f"supply_ratio_{years}y"
    if view.stock is None:
        return Feature.missing(
            key, f"기존 stock 을 구하지 못했습니다 ({view.basis}). "
                 f"절대물량으로 대체하지 않습니다")
    value = view.ratio(years)
    if value is None:
        return Feature.missing(key, "공급 비율을 계산하지 못했습니다")

    by_cat: dict[str, float] = {}
    for item in view.items:
        if item.move_in_ym <= _shift(view.end_ym or "190001", years * 12):
            by_cat[item.category] = by_cat.get(item.category, 0.0) + item.effective

    # 계획 단계 비중이 높으면 그만큼 덜 믿는다
    planned = sum(i.effective for i in view.items if i.stage in ("계획", "분양"))
    total = sum(i.effective for i in view.items) or 1.0
    conf = combine(0.9 if view.items else 0.4, 1.0 - 0.5 * (planned / total))

    calc = Calc(
        value=value, unit="비율",
        formula="Supply Ratio = 실효 입주물량 ÷ 기존 주택 stock",
        inputs={"기간": f"{years}년", "stock": f"{view.stock:,}세대", "기준": view.basis},
        intermediates={
            "실효 물량": f"{view.by_horizon.get(years, 0):,.0f}세대",
            "원 물량": f"{view.raw_by_horizon.get(years, 0):,}세대",
            "단계 가중치": STAGE_WEIGHT,
            "가중치 성격": STAGE_NOTE,
            "경쟁 분류별": {k: f"{v:,.0f}세대" for k, v in sorted(by_cat.items())},
            "해석": "같은 물량이라도 stock 이 작은 지역에서 충격이 크다. "
                  "절대물량으로 비교하면 큰 도시가 늘 공급과다로 나온다",
        },
        grade="ESTIMATED",
    )
    return Feature(key, value, "", conf, Status.OK, calc.intermediates,
                   calc).with_confidence(conf)


def cliff_feature(view: SupplyView) -> Feature:
    """Supply Cliff — 공급이 끝나는가 (§13).

    값이 클수록 "지금은 많은데 곧 끊긴다". 공급 공백은 그 뒤 가격의 재료가 된다.
    """
    key = "supply_cliff"
    near = view.by_horizon.get(2)
    far = view.by_horizon.get(5)
    if near is None or far is None:
        return Feature.missing(key, "공급 시계열이 없습니다")
    if near <= 0:
        return Feature(key, 0.0, "", 0.5, Status.OK,
                       {"판정": "향후 2년 공급이 없어 절벽이랄 것도 없다"})

    later = far - near                      # 3~5년차 물량
    drop = 1.0 - (later / near) if near else 0.0
    value = max(0.0, min(1.0, drop))
    calc = Calc(
        value=value, unit="0~1",
        formula="Supply Cliff = 1 − (3~5년차 물량 ÷ 향후 2년 물량)",
        inputs={"향후 2년": f"{near:,.0f}세대", "3~5년차": f"{later:,.0f}세대"},
        intermediates={
            "판정": ("공급 절벽 — 지금은 많지만 곧 끊긴다"
                   if value >= CLIFF_DROP else "완만"),
            "기준": f"{CLIFF_DROP:.0%} 이상 감소를 절벽으로 본다 (판정 기준, 백테스트가 대체)",
            "주의": "먼 미래 물량은 아직 발표되지 않았을 뿐일 수 있다. "
                   "절벽처럼 보이는 것이 정보 부족일 가능성을 배제하지 못한다",
        },
        grade="ESTIMATED",
    )
    # 먼 미래일수록 계획이 덜 나와 있어 신뢰도를 낮춘다
    return Feature(key, value, "", 0.5, Status.OK, calc.intermediates,
                   calc).with_confidence(0.5)


def all_features(conn: sqlite3.Connection, complex_id: int, *,
                 as_of: cutoff_mod.AsOf) -> list[Feature]:
    view = build(conn, complex_id, as_of=as_of)
    return [ratio_feature(view, y) for y in HORIZONS] + [cliff_feature(view)]


def _shift(ym: str, months: int) -> str:
    total = int(ym[:4]) * 12 + (int(ym[4:6]) - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"
